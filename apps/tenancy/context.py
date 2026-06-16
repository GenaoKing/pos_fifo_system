from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings


_tenant_key = ContextVar('tenant_key', default=None)
_tenant_alias = ContextVar('tenant_alias', default=None)
_force_enabled = ContextVar('tenancy_force_enabled', default=False)


class TenantContextError(RuntimeError):
    pass


def tenancy_enabled():
    return bool(
        getattr(settings, 'TENANCY_DB_PER_TENANT_ENABLED', False)
        or _force_enabled.get()
    )


def get_current_tenant_key():
    return _tenant_key.get()


def get_current_tenant_alias():
    return _tenant_alias.get()


def set_current_tenant(tenant_key, alias):
    key_token = _tenant_key.set(tenant_key)
    alias_token = _tenant_alias.set(alias)
    return key_token, alias_token


def reset_current_tenant(tokens):
    if not tokens:
        return
    key_token, alias_token = tokens
    _tenant_alias.reset(alias_token)
    _tenant_key.reset(key_token)


def clear_current_tenant():
    _tenant_alias.set(None)
    _tenant_key.set(None)


def bind_tenant_context_to_request(request, tokens):
    request._tenant_context_tokens = tokens
    django_request = getattr(request, '_request', None)
    if django_request is not None:
        django_request._tenant_context_tokens = tokens


@contextmanager
def force_tenancy(enabled=True):
    token = _force_enabled.set(bool(enabled))
    try:
        yield
    finally:
        _force_enabled.reset(token)


@contextmanager
def tenant_context(tenant_or_key):
    from .registry import configure_tenant_database

    tenant, alias = configure_tenant_database(tenant_or_key)
    tokens = set_current_tenant(tenant.tenant_key, alias)
    try:
        yield tenant
    finally:
        reset_current_tenant(tokens)
