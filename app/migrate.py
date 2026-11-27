"""Migration runner: applies pending SQL files in migrations/ idempotently."""
from pathlib import Path
from app.db import get_conn

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"


def applied_versions(conn) -> set[str]:
    conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations(
      version TEXT PRIMARY KEY, applied_at TEXT DEFAULT (datetime('now')))""")
    return {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}


def apply_pending(db_path: str | None = None) -> list[str]:
    done: list[str] = []
    with get_conn(db_path) as conn:
        applied = applied_versions(conn)
        for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
            ver = f.stem
            if ver in applied:
                continue
            sql = f.read_text()
            conn.executescript(sql)
            conn.execute("INSERT OR IGNORE INTO schema_migrations(version) VALUES (?)", (ver,))
            done.append(ver)
    # One-time env->DB settings bootstrap so an upgrade from the env-configured
    # deployment keeps its effective values; the settings page owns them after.
    try:
        from app.config import bootstrap_env_to_db
        bootstrap_env_to_db(db_path)
    except Exception:
        pass
    return done


if __name__ == "__main__":
    print(apply_pending())
