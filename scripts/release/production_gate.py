"""Production maintenance and health gates; never opens ingress or rolls back.

Only ``local-health`` runs inside the container. Other commands use read-only
Azure CLI operations. Evidence deliberately excludes environment values and
secrets from raw Azure responses.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import sys
import time
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import uuid


MARKER = "POSFIFO_RELEASE_HEALTH="


class GateError(RuntimeError):
    pass


def azure_json(*arguments):
    result = subprocess.run(
        ["az", *arguments, "--only-show-errors", "--output", "json"],
        capture_output=True, text=True, timeout=90, check=False,
    )
    if result.returncode:
        # Do not echo raw Azure responses: they may contain environment values.
        raise GateError(f"Azure read failed ({arguments[:3]}, exit {result.returncode}).")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise GateError("Azure returned invalid JSON.") from exc


def write_evidence(path, payload):
    if path:
        destination = Path(path)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def snapshot(app, revisions):
    properties = app.get("properties", {})
    configuration = properties.get("configuration")
    if not isinstance(configuration, dict) or not isinstance(revisions, list):
        raise GateError("Cannot prove maintenance: incomplete app/revision metadata.")
    active = []
    for revision in revisions:
        is_active = revision.get("properties", {}).get("active")
        if not isinstance(is_active, bool):
            raise GateError("Cannot prove maintenance: revision activity is unknown.")
        if is_active:
            active.append(revision["name"])
    template = properties.get("template", {})
    containers = template.get("containers", [])
    primary = containers[0] if containers else {}
    return {
        "app": app.get("name"),
        "ingress_disabled": configuration.get("ingress") is None,
        "active_revisions": active,
        "latest_revision": properties.get("latestRevisionName"),
        "image": primary.get("image"),
        "runtime_sha": next((item.get("value") for item in primary.get("env", [])
                             if item.get("name") == "GIT_COMMIT_SHA"), None),
        "min_replicas": template.get("scale", {}).get("minReplicas", 0),
    }


def inspect_app(resource_group, app_name):
    common = ("--resource-group", resource_group, "--name", app_name)
    app = azure_json("containerapp", "show", *common)
    revisions = azure_json("containerapp", "revision", "list", *common, "--all")
    return snapshot(app, revisions)


def require_frozen(state):
    if not state["ingress_disabled"]:
        raise GateError("Production ingress must be absent, not merely internal.")
    if state["active_revisions"]:
        raise GateError("All API revisions must be deactivated before production migrations.")


def require_new_revision(state, expected_sha, expected_image):
    if not state["ingress_disabled"]:
        raise GateError("Production ingress was reopened before operator acceptance.")
    if not state["latest_revision"] or state["active_revisions"] != [state["latest_revision"]]:
        raise GateError("Only the new API revision may be active during internal verification.")
    if state["image"] != expected_image or state["runtime_sha"] != expected_sha:
        raise GateError("API image/runtime SHA does not match the approved release.")
    if state["min_replicas"] < 1:
        raise GateError("Internal verification requires minReplicas >= 1.")


def require_revision_template(revision, expected_sha, expected_image):
    properties = revision.get("properties", {})
    containers = properties.get("template", {}).get("containers", [])
    if properties.get("active") is not True or not containers:
        raise GateError("The selected API revision is not active or has no container metadata.")
    primary = containers[0]
    sha = next((item.get("value") for item in primary.get("env", [])
                if item.get("name") == "GIT_COMMIT_SHA"), None)
    if primary.get("image") != expected_image or sha != expected_sha:
        raise GateError("The actual API revision template differs from the approved image/SHA.")


def migration_budget(configuration):
    timeout = configuration.get("replicaTimeout")
    retries = configuration.get("replicaRetryLimit")
    if type(timeout) is not int or timeout <= 0 or type(retries) is not int or retries < 0:
        raise GateError("Migration replicaTimeout/replicaRetryLimit must be explicit valid integers.")
    budget = timeout * (retries + 1) + 600
    if budget > 6600:
        raise GateError("Migration budget exceeds the deployment job window; adjust the runbook first.")
    return budget


def wait_migration(resource_group, job_name, execution, evidence):
    common = ("--resource-group", resource_group, "--name", job_name)
    job = azure_json("containerapp", "job", "show", *common)
    budget = migration_budget(job.get("properties", {}).get("configuration", {}))
    record = {"job": job_name, "execution": execution, "budget_seconds": budget, "states": []}
    deadline = time.monotonic() + budget
    print(f"Migration budget: {budget}s; execution: {execution}", flush=True)
    while time.monotonic() < deadline:
        try:
            result = azure_json("containerapp", "job", "execution", "show", *common,
                                "--job-execution-name", execution)
            props = result.get("properties", {})
            status = props.get("status", "Unknown")
            record["states"].append({
                "status": status, "observed_at": time.time(),
                "start_time": props.get("startTime"), "end_time": props.get("endTime"),
            })
        except (GateError, subprocess.TimeoutExpired):
            status = "Unknown"
            record["states"].append({"status": status, "observed_at": time.time()})
        write_evidence(evidence, record)
        print(f"Migration status: {status}", flush=True)
        if status == "Succeeded":
            return
        if status in {"Failed", "Degraded", "Cancelled", "Canceled", "Stopped"}:
            raise GateError(f"Migration ended {status}; leave production closed and use fix-forward.")
        time.sleep(10)
    record["timed_out"] = True
    write_evidence(evidence, record)
    raise GateError("Migration wait timed out; execution may still run. Leave production closed.")


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise GateError("Internal health must not redirect to public ingress.")


def local_health(expected_sha, host, nonce):
    if not re.fullmatch(r"[0-9a-f]{40}", expected_sha):
        raise GateError("Expected SHA must be a full Git revision.")
    if not re.fullmatch(r"[A-Za-z0-9.-]+", host):
        raise GateError("Invalid health Host header.")
    results = {}
    # The original public Host is allowed by Django; the request itself stays
    # on loopback. Proxy scheme avoids SECURE_SSL_REDIRECT to disabled ingress.
    opener = build_opener(ProxyHandler({}), NoRedirect())
    for path in ("/api/v1/health/", "/api/v1/health/live/"):
        request = Request("http://127.0.0.1:8000" + path,
                          headers={"Host": host, "X-Forwarded-Proto": "https"})
        with opener.open(request, timeout=20) as response:
            payload = json.load(response)
            if response.status != 200 or payload.get("status") != "ok":
                raise GateError(f"Internal health failed: {path}.")
        if payload.get("commit") != expected_sha or payload.get("environment") != "prod":
            raise GateError(f"Unexpected runtime identity at {path}.")
        if path == "/api/v1/health/" and payload.get("db") != "ok":
            raise GateError("Internal database health is not OK.")
        results[path] = payload
    print(MARKER + json.dumps({"ok": True, "nonce": nonce, "sha": expected_sha, "health": results}), flush=True)


def parse_health_marker(output, nonce, expected_sha):
    # Exec can return zero even when the remote command fails. A matching JSON
    # marker, not the CLI exit code or echoed command, proves remote completion.
    decoder = json.JSONDecoder()
    for part in output.split(MARKER)[1:]:
        try:
            payload, _ = decoder.raw_decode(part.lstrip())
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("ok") is True and payload.get("nonce") == nonce and payload.get("sha") == expected_sha:
            return payload
    raise GateError("No successful health marker from the selected API replica.")


def terminal_run(command, timeout=90):
    """Run az exec with a PTY and keep its input open until remote completion.

    Piping empty stdin makes Azure's interactive writer exit prematurely. A
    private PTY supplies terminal attributes and a live input without a shell.
    This path is only used on Linux Actions runners.
    """
    import errno
    import pty
    import select
    import signal

    master, slave = pty.openpty()
    process = None
    output = []
    try:
        process = subprocess.Popen(command, stdin=slave, stdout=slave, stderr=slave,
                                   start_new_session=True)
        os.close(slave)
        slave = None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            readable, _, _ = select.select([master], [], [], 0.2)
            if readable:
                try:
                    data = os.read(master, 65536)
                except OSError as exc:
                    if exc.errno == errno.EIO:
                        break
                    raise
                if not data:
                    break
                output.append(data)
            elif process.poll() is not None:
                break
        else:
            raise GateError("Azure exec exceeded the internal health timeout.")
        process.wait(timeout=5)
        return b"".join(output).decode("utf-8", errors="replace")
    finally:
        if process is not None and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
        os.close(master)
        if slave is not None:
            os.close(slave)


def internal_health(resource_group, app_name, expected_sha, expected_image, base_url, evidence):
    host = urlsplit(base_url).hostname
    if not host:
        raise GateError("Production API URL must contain a hostname.")
    last_error = "No replica checked."
    for attempt in range(1, 19):
        try:
            state = inspect_app(resource_group, app_name)
            require_new_revision(state, expected_sha, expected_image)
            revision = azure_json("containerapp", "revision", "show", "--resource-group", resource_group,
                                  "--name", app_name, "--revision", state["latest_revision"])
            require_revision_template(revision, expected_sha, expected_image)
            replicas = azure_json("containerapp", "replica", "list", "--resource-group", resource_group,
                                  "--name", app_name, "--revision", state["latest_revision"])
            if not replicas:
                raise GateError("New API replica is not available yet.")
            replica = replicas[0]
            containers = replica.get("properties", {}).get("containers", [])
            if not containers:
                raise GateError("New API container is not available yet.")
            nonce = uuid.uuid4().hex
            remote = shlex.join(["python", "/app/scripts/release/production_gate.py", "local-health",
                                 "--expected-sha", expected_sha, "--host", host, "--nonce", nonce])
            command = ["az", "containerapp", "exec", "--resource-group", resource_group,
                       "--name", app_name, "--revision", state["latest_revision"],
                       "--replica", replica["name"], "--container", containers[0]["name"],
                       "--command", remote, "--only-show-errors"]
            marker = parse_health_marker(terminal_run(command), nonce, expected_sha)
            final_state = inspect_app(resource_group, app_name)
            require_new_revision(final_state, expected_sha, expected_image)
            if final_state["latest_revision"] != state["latest_revision"]:
                raise GateError("API revision changed during health verification.")
            write_evidence(evidence, {"state": final_state, "replica": replica["name"], "result": marker})
            print("Internal production health verified; ingress remains disabled.")
            return
        except (GateError, subprocess.TimeoutExpired) as exc:
            last_error = str(exc)
            write_evidence(evidence, {"attempt": attempt, "error": last_error})
            print(f"Internal health attempt {attempt}/18: {last_error}", flush=True)
            time.sleep(10)
    raise GateError(last_error)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("frozen", "snapshot", "internal-health"):
        sub = commands.add_parser(name)
        sub.add_argument("--resource-group", required=True)
        sub.add_argument("--app-name", required=True)
        sub.add_argument("--evidence", required=True)
        if name == "internal-health":
            sub.add_argument("--expected-sha", required=True)
            sub.add_argument("--expected-image", required=True)
            sub.add_argument("--base-url", required=True)
    sub = commands.add_parser("wait-migration")
    sub.add_argument("--resource-group", required=True)
    sub.add_argument("--job-name", required=True)
    sub.add_argument("--execution", required=True)
    sub.add_argument("--evidence", required=True)
    sub = commands.add_parser("local-health")
    sub.add_argument("--expected-sha", required=True)
    sub.add_argument("--host", required=True)
    sub.add_argument("--nonce", required=True)
    args = vars(parser.parse_args())
    command = args.pop("command")
    if command in {"frozen", "snapshot"}:
        state = inspect_app(args["resource_group"], args["app_name"])
        write_evidence(args["evidence"], state)
        if command == "frozen":
            require_frozen(state)
        print(json.dumps(state))
    else:
        globals()[command.replace("-", "_")](**args)


if __name__ == "__main__":
    try:
        main()
    except (GateError, subprocess.TimeoutExpired) as exc:
        print(f"Release gate failed: {exc}", file=sys.stderr)
        sys.exit(1)
