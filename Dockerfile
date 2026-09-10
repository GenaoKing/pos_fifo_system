FROM python:3.12-slim-bookworm@sha256:782412e85d0f0984994c290652577d4018aff08145c85b262bb63dc0c7522254

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings_cloud \
    PORT=8000

WORKDIR /app

RUN useradd --create-home --shell /usr/sbin/nologin appuser

COPY requirements_cloud.txt /app/
RUN python -m pip install --no-cache-dir --upgrade "pip==26.0.1" \
    && python -m pip install --no-cache-dir --require-hashes -r requirements_cloud.txt

COPY --chown=appuser:appuser . /app/

RUN DJANGO_SECRET_KEY="build-only-secret-key-with-more-than-fifty-characters-1234567890" \
    ALLOWED_HOSTS="localhost,127.0.0.1" \
    DB_NAME="build_only" \
    DB_USER="build_only" \
    DB_PASSWORD="build_only" \
    DB_HOST="localhost" \
    DB_PORT="5432" \
    DB_SSLMODE="disable" \
    CORS_ALLOWED_ORIGINS="http://localhost:5173" \
    CLOUD_ENVIRONMENT="build" \
    APP_VERSION="build" \
    GIT_COMMIT_SHA="build" \
    python manage.py collectstatic --noinput --settings=config.settings_cloud

USER appuser

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
