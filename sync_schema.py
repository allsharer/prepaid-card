"""Sync schema for prepaid_card.db.

Additive migration that lets the SQLite database be two-way synced with the
Android app without changing app.py or prepaid_tracker.py:

  * `uid`        TEXT  — stable per-row id (UUID), shared across devices
  * `updated_at` TEXT  — ISO-8601 UTC, bumped on every INSERT/UPDATE
  * a `tombstones` table — records deletes so they can propagate
  * triggers that keep the above populated for rows created by the web / CLI app

Run `python sync_schema.py` once (safe to re-run). It is also imported by
sync_server.py which calls ensure_schema() on startup.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prepaid_card.db")

NOW_SQL = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"

# A v4-ish UUID built from randomblob(); uniqueness is all that matters here.
UUID_SQL = (
    "lower("
    "hex(randomblob(4)) || '-' || hex(randomblob(2)) || '-4' || "
    "substr(hex(randomblob(2)), 2) || '-' || "
    "substr('89ab', 1 + (abs(random()) % 4), 1) || substr(hex(randomblob(2)), 2) || "
    "'-' || hex(randomblob(6))"
    ")"
)

TABLES = ("companies", "account")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def ensure_schema(db_path=DB_PATH):
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS tombstones (
                uid        TEXT PRIMARY KEY,
                tbl        TEXT NOT NULL,
                deleted_at TEXT NOT NULL
            );
            """
        )

        for table in TABLES:
            cols = _columns(conn, table)
            if "uid" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN uid TEXT")
            if "updated_at" not in cols:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")

            # Backfill existing rows.
            conn.execute(
                f"UPDATE {table} SET uid = ({UUID_SQL}) WHERE uid IS NULL OR uid = ''"
            )
            conn.execute(
                f"UPDATE {table} SET updated_at = {NOW_SQL} "
                f"WHERE updated_at IS NULL OR updated_at = ''"
            )
            conn.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS idx_{table}_uid ON {table}(uid)"
            )

            # Rows created by app.py / prepaid_tracker.py have no uid/updated_at —
            # fill them in. Rows created by the sync server pass an explicit uid
            # (WHEN guard skips them).
            conn.execute(f"DROP TRIGGER IF EXISTS trg_{table}_ai")
            conn.execute(
                f"""
                CREATE TRIGGER trg_{table}_ai AFTER INSERT ON {table}
                FOR EACH ROW WHEN NEW.uid IS NULL
                BEGIN
                    UPDATE {table}
                       SET uid = ({UUID_SQL}), updated_at = {NOW_SQL}
                     WHERE refno = NEW.refno;
                    DELETE FROM tombstones WHERE uid = (
                        SELECT uid FROM {table} WHERE refno = NEW.refno
                    );
                END;
                """
            )

            # Bump updated_at on local edits. Sync-driven updates set updated_at
            # explicitly (NEW != OLD) so the guard leaves their timestamp intact.
            conn.execute(f"DROP TRIGGER IF EXISTS trg_{table}_au")
            conn.execute(
                f"""
                CREATE TRIGGER trg_{table}_au AFTER UPDATE ON {table}
                FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
                BEGIN
                    UPDATE {table} SET updated_at = {NOW_SQL} WHERE refno = NEW.refno;
                END;
                """
            )

            # Capture deletes as tombstones so they can be synced out.
            conn.execute(f"DROP TRIGGER IF EXISTS trg_{table}_ad")
            conn.execute(
                f"""
                CREATE TRIGGER trg_{table}_ad AFTER DELETE ON {table}
                FOR EACH ROW
                BEGIN
                    INSERT OR REPLACE INTO tombstones (uid, tbl, deleted_at)
                    VALUES (OLD.uid, '{table}', {NOW_SQL});
                END;
                """
            )

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    ensure_schema()
    conn = sqlite3.connect(DB_PATH)
    for table in TABLES:
        n, m = conn.execute(
            f"SELECT COUNT(*), COUNT(uid) FROM {table}"
        ).fetchone()
        print(f"{table}: {n} rows, {m} with uid")
    print("tombstones:", conn.execute("SELECT COUNT(*) FROM tombstones").fetchone()[0])
    conn.close()
    print("schema ready.")
