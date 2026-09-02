from pathlib import Path
from .db import connect

MIGRATIONS_DIR = Path('/app/migrations')


def apply_migrations():
    if not MIGRATIONS_DIR.exists():
        return
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS schema_migrations (filename text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
        # Existing starter databases were initialized by docker-entrypoint before migration tracking existed.
        products_exists = conn.execute("SELECT to_regclass('public.products') IS NOT NULL").fetchone()[0]
        if products_exists:
            conn.execute("INSERT INTO schema_migrations(filename) VALUES ('001_schema.sql') ON CONFLICT DO NOTHING")
        applied = {r[0] for r in conn.execute('SELECT filename FROM schema_migrations')}
        for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
            if path.name in applied:
                continue
            sql = path.read_text(encoding='utf-8')
            conn.execute(sql)
            conn.execute('INSERT INTO schema_migrations(filename) VALUES (%s)', (path.name,))
        conn.commit()
