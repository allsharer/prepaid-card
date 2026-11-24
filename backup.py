"""Database backup / restore shared by the terminal app, web app and sync server.

Backups live in the same folder `gkone --backup / --list-backups / --restore`
uses, with the same names:

    prepaid_card_<YYYYmmdd_HHMMSS>.db               manual backup
    prepaid_card_<YYYYmmdd_HHMMSS>_pre-restore.db   saved automatically before a restore
    prepaid_card_<YYYYmmdd_HHMMSS>_pre-sync.db      saved automatically before a phone sync

Copies are made with SQLite's backup API, so they are consistent even while
another program has the database open.
"""
import glob
import os
import sqlite3
import time
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prepaid_card.db")
BACKUP_DIR = os.path.expanduser("~/.local/share/prepaid-card/backups")
KEEP_BACKUPS = 10           # manual + pre-restore copies (same rule as gkone --backup)
KEEP_PRE_SYNC_BACKUPS = 10  # automatic pre-sync copies, counted separately

PRE_SYNC = "_pre-sync"
PRE_RESTORE = "_pre-restore"


def _copy_db(src_path, dst_path):
    src = sqlite3.connect(src_path)
    dst = sqlite3.connect(dst_path)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()


def _new_path(suffix=""):
    os.makedirs(BACKUP_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"prepaid_card_{stamp}{suffix}.db")
    n = 2
    while os.path.exists(path):  # two backups in the same second
        path = os.path.join(BACKUP_DIR, f"prepaid_card_{stamp}-{n}{suffix}.db")
        n += 1
    return path


def _all_files():
    return sorted(
        glob.glob(os.path.join(BACKUP_DIR, "prepaid_card_*.db")),
        key=os.path.getmtime,
        reverse=True,
    )


def _prune():
    files = _all_files()
    pre_sync = [f for f in files if f.endswith(PRE_SYNC + ".db")]
    others = [f for f in files if not f.endswith(PRE_SYNC + ".db")]
    for f in others[KEEP_BACKUPS:] + pre_sync[KEEP_PRE_SYNC_BACKUPS:]:
        try:
            os.remove(f)
            # gkone keeps a sync-token copy next to its own backups
            stem = os.path.basename(f)[len("prepaid_card_"):-len(".db")]
            tok = os.path.join(BACKUP_DIR, f"sync_token_{stem}")
            if os.path.exists(tok):
                os.remove(tok)
        except OSError:
            pass


def make_backup(suffix="", db_path=DB_PATH, prune=True):
    """Back up the database; returns the new file's path. Raises on failure."""
    path = _new_path(suffix)
    _copy_db(db_path, path)
    if prune:
        _prune()
    return path


def kind_of(name):
    if name.endswith(PRE_SYNC + ".db"):
        return "Before phone sync"
    if name.endswith(PRE_RESTORE + ".db"):
        return "Before a restore"
    return "Backup"


def _summary(path):
    """(transaction count, balance) of a backup, or (None, None) if unreadable."""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            n, bal = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(CASE WHEN type='Receipt' "
                "THEN amount ELSE -amount END), 0) FROM account"
            ).fetchone()
            return n, bal
        finally:
            conn.close()
    except sqlite3.Error:
        return None, None


def list_backups():
    """Newest first: dicts with name, path, when (datetime), size, kind, count, balance."""
    out = []
    for f in _all_files():
        name = os.path.basename(f)
        count, balance = _summary(f)
        out.append({
            "name": name,
            "path": f,
            "when": datetime.fromtimestamp(os.path.getmtime(f)),
            "size": os.path.getsize(f),
            "kind": kind_of(name),
            "count": count,
            "balance": balance,
        })
    return out


def find_backup(name):
    """Resolve a backup by file name only (never a path); None if not found."""
    if not name or os.path.basename(name) != name:
        return None
    for f in _all_files():
        if os.path.basename(f) == name:
            return f
    return None


def restore_backup(name, db_path=DB_PATH):
    """Replace the database with backup `name`.

    The current database is first saved as a *_pre-restore backup, so a restore
    can itself be undone. Returns that safety backup's path.
    """
    path = find_backup(name)
    if path is None:
        raise FileNotFoundError(f"No backup named {name}")
    if _summary(path)[0] is None:
        raise ValueError(f"{name} is not a readable prepaid-card database")

    # Prune only after copying, so the backup being restored can't be pruned away.
    safety = make_backup(PRE_RESTORE, db_path, prune=False)
    _copy_db(path, db_path)
    _prune()

    # Older backups may predate the sync columns; add them back.
    try:
        from sync_schema import ensure_schema
        ensure_schema(db_path)
    except Exception:
        pass
    return safety
