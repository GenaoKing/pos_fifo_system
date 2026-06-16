from django.conf import settings


def database_exists(db_name):
    import psycopg

    conn = _maintenance_connection()
    try:
        with conn.cursor() as cur:
            cur.execute('SELECT 1 FROM pg_database WHERE datname = %s', (db_name,))
            return cur.fetchone() is not None
    finally:
        conn.close()


def create_database(db_name):
    import psycopg
    from psycopg import sql

    conn = _maintenance_connection()
    try:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(
                sql.SQL('CREATE DATABASE {}').format(sql.Identifier(db_name))
            )
    finally:
        conn.close()


def _maintenance_connection():
    import psycopg

    default = settings.DATABASES['default']
    options = default.get('OPTIONS', {})
    kwargs = {
        'dbname': default.get('MAINTENANCE_DB') or 'postgres',
        'user': default.get('USER') or None,
        'password': default.get('PASSWORD') or None,
        'host': default.get('HOST') or None,
        'port': default.get('PORT') or None,
    }
    sslmode = options.get('sslmode')
    if sslmode:
        kwargs['sslmode'] = sslmode
    kwargs = {key: value for key, value in kwargs.items() if value not in (None, '')}
    return psycopg.connect(**kwargs)
