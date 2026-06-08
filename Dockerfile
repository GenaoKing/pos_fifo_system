FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DJANGO_SETTINGS_MODULE=config.settings_cloud \
    PORT=8000

WORKDIR /app

COPY requirements_cloud.txt /app/
RUN pip install --upgrade pip \
    && pip install -r requirements_cloud.txt

COPY . /app/

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

RUN useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "120", "--access-logfile", "-", "--error-logfile", "-"]
