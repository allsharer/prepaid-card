"""Two-way sync receiver for the GKOne Prepaid Card Android app.

The phone pushes its full dataset to `POST /sync`; this merges it into the
local `prepaid_card.db` (last-write-wins per row, tombstones for deletes) and
returns the rows/tombstones the phone is missing or has an older copy of.
The web app (app.py) and the terminal app keep using the same database
unchanged.

Run it so the phone can reach it over Tailscale:

    ./sync-server                 # binds to this host's Tailscale IP, port 8765

Auth is a bearer token kept in `.sync_token` (created on first run). Enter the
same URL + token in the Android app's Sync screen.
"""
import os
import secrets
import sqlite3
import subprocess
import sys

from flask import Flask, jsonify, request

import backup
from sync_schema import DB_PATH, NOW_SQL, ensure_schema

HERE = os.path.dirname(os.path.abspath(__file__))
TOKEN_PATH = os.path.join(HERE, ".sync_token")
PORT = int(os.environ.get("SYNC_PORT", "8765"))
BACKUP_DIR = backup.BACKUP_DIR

# A new transaction from the phone counts as a duplicate of one already here
# when type, payee/payer and amount match and the dates are this close.
DUP_WINDOW_DAYS = 3

app = Flask(__name__)


def load_token():
    if not os.path.exists(TOKEN_PATH):
        tok = secrets.token_urlsafe(24)
        with open(TOKEN_PATH, "w") as fh:
            fh.write(tok + "\n")
        os.chmod(TOKEN_PATH, 0o600)
        print(f"generated new sync token -> {TOKEN_PATH}")
    with open(TOKEN_PATH) as fh:
        return fh.read().strip()


TOKEN = load_token()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _authorised(req):
    hdr = req.headers.get("Authorization", "")
    return hdr.startswith("Bearer ") and secrets.compare_digest(hdr[7:], TOKEN)


def _tombstone_map(conn):
    return {r["uid"]: r["deleted_at"] for r in conn.execute("SELECT uid, deleted_at FROM tombstones")}


def backup_before_sync():
    """Back up the database (keeping the newest pre-sync copies).

    Returns the backup path. Raises on failure so the sync is not applied
    without a backup.
    """
    return backup.make_backup(backup.PRE_SYNC, DB_PATH)


def _find_duplicate(conn, a, company_refno, phone_uids, claimed):
    """Return a local account row that `a` (new from the phone) duplicates, or None.

    Only local rows the phone doesn't already have are candidates: if the phone
    knows a row, the two are separate transactions it has seen side by side.
    """
    rows = conn.execute(
        "SELECT refno, uid, date, details FROM account "
        "WHERE type = ? AND company = ? AND ABS(amount - ?) < 0.005 "
        "AND ABS(julianday(date) - julianday(?)) <= ? "
        "ORDER BY ABS(julianday(date) - julianday(?)), refno",
        (a["type"], company_refno, float(a["amount"]), a["date"],
         DUP_WINDOW_DAYS, a["date"]),
    ).fetchall()
    for r in rows:
        if r["uid"] not in phone_uids and r["refno"] not in claimed:
            return r
    return None


@app.get("/ping")
def ping():
    return jsonify(ok=True, service="prepaid-card-sync")


@app.post("/sync")
def sync():
    if not _authorised(request):
        return jsonify(error="unauthorized"), 401

    payload = request.get_json(force=True, silent=True) or {}
    in_companies = payload.get("companies", [])
    in_account = payload.get("account", [])
    in_tombstones = payload.get("tombstones", [])

    try:
        backup_path = backup_before_sync()
    except Exception as e:
        print(f"sync refused: backup failed: {e}", file=sys.stderr)
        return jsonify(error=f"backup failed, nothing synced: {e}"), 500
    print(f"pre-sync backup: {backup_path}")

    # Every uid the phone holds (live or deleted) — used for duplicate checks.
    phone_uids = {a.get("uid") for a in in_account if a.get("uid")} | {
        t.get("uid") for t in in_tombstones if t.get("uid")
    }
    claimed = set()      # local refnos already matched to a phone duplicate
    duplicates = []

    applied = {"companies": 0, "account": 0, "tombstones": 0, "duplicates": 0, "errors": 0}
    conn = db()
    try:
        # 1. incoming tombstones -----------------------------------------------
        for t in in_tombstones:
            uid, tbl, del_at = t.get("uid"), t.get("tbl"), t.get("deleted_at")
            if tbl not in ("companies", "account") or not uid or not del_at:
                continue
            row = conn.execute(
                f"SELECT updated_at FROM {tbl} WHERE uid = ?", (uid,)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT OR IGNORE INTO tombstones (uid, tbl, deleted_at) VALUES (?,?,?)",
                    (uid, tbl, del_at),
                )
                applied["tombstones"] += 1
            elif (row["updated_at"] or "") <= del_at:
                conn.execute(f"DELETE FROM {tbl} WHERE uid = ?", (uid,))
                conn.execute(
                    "UPDATE tombstones SET deleted_at = ? WHERE uid = ?", (del_at, uid)
                )
                applied["tombstones"] += 1
            # else: local row is newer than the delete -> keep it

        tomb = _tombstone_map(conn)

        # 2. incoming companies (LWW) ----------------------------------------
        for c in in_companies:
            uid = c.get("uid")
            if not uid:
                continue
            ts = c.get("updated_at") or ""
            if uid in tomb and tomb[uid] >= ts:
                continue
            try:
                row = conn.execute(
                    "SELECT updated_at FROM companies WHERE uid = ?", (uid,)
                ).fetchone()
                if row is None:
                    conn.execute(
                        "INSERT INTO companies (uid, name, type, updated_at) VALUES (?,?,?,?)",
                        (uid, c["name"], c["type"], ts),
                    )
                    conn.execute("DELETE FROM tombstones WHERE uid = ?", (uid,))
                    applied["companies"] += 1
                elif ts > (row["updated_at"] or ""):
                    conn.execute(
                        "UPDATE companies SET name = ?, type = ?, updated_at = ? WHERE uid = ?",
                        (c["name"], c["type"], ts, uid),
                    )
                    applied["companies"] += 1
            except (sqlite3.Error, KeyError):
                applied["errors"] += 1

        # 3. incoming account rows (resolve company by uid) -----------------
        for a in in_account:
            uid = a.get("uid")
            if not uid:
                continue
            ts = a.get("updated_at") or ""
            if uid in tomb and tomb[uid] >= ts:
                continue
            comp = conn.execute(
                "SELECT refno FROM companies WHERE uid = ?", (a.get("company_uid"),)
            ).fetchone()
            if comp is None:
                applied["errors"] += 1
                continue
            try:
                row = conn.execute(
                    "SELECT updated_at FROM account WHERE uid = ?", (uid,)
                ).fetchone()
                vals = (
                    a["date"], a["type"], comp["refno"],
                    a.get("details"), float(a["amount"]), ts,
                )
                if row is None:
                    dup = _find_duplicate(conn, a, comp["refno"], phone_uids, claimed)
                    if dup is not None:
                        # Keep the computer's copy; tombstone the phone's so the
                        # phone drops it and takes ours from the reply instead.
                        claimed.add(dup["refno"])
                        conn.execute(
                            f"INSERT OR REPLACE INTO tombstones (uid, tbl, deleted_at) "
                            f"VALUES (?, 'account', {NOW_SQL})",
                            (uid,),
                        )
                        duplicates.append({
                            "phone": {"date": a["date"], "details": a.get("details"),
                                      "amount": float(a["amount"])},
                            "kept_refno": dup["refno"], "kept_date": dup["date"],
                            "kept_details": dup["details"],
                        })
                        applied["duplicates"] += 1
                        print(f"skipped duplicate: phone {a['date']} {a.get('details')!r} "
                              f"{a['amount']} = local #{dup['refno']} {dup['date']} "
                              f"{dup['details']!r}")
                        continue
                    conn.execute(
                        "INSERT INTO account (date, type, company, details, amount, updated_at, uid) "
                        "VALUES (?,?,?,?,?,?,?)",
                        vals + (uid,),
                    )
                    conn.execute("DELETE FROM tombstones WHERE uid = ?", (uid,))
                    applied["account"] += 1
                elif ts > (row["updated_at"] or ""):
                    conn.execute(
                        "UPDATE account SET date=?, type=?, company=?, details=?, amount=?, updated_at=? "
                        "WHERE uid = ?",
                        vals + (uid,),
                    )
                    applied["account"] += 1
            except (sqlite3.Error, KeyError, TypeError, ValueError):
                applied["errors"] += 1

        conn.commit()

        # 4. build the reply: whatever the phone lacks or has older ---------
        client_c = {c["uid"]: (c.get("updated_at") or "") for c in in_companies if c.get("uid")}
        client_a = {a["uid"]: (a.get("updated_at") or "") for a in in_account if a.get("uid")}
        client_t = {t.get("uid") for t in in_tombstones if t.get("uid")}

        out_companies = [
            {"uid": r["uid"], "name": r["name"], "type": r["type"],
             "updated_at": r["updated_at"]}
            for r in conn.execute("SELECT uid, name, type, updated_at FROM companies")
            if r["uid"] not in client_c or (r["updated_at"] or "") > client_c[r["uid"]]
        ]
        out_account = [
            {"uid": r["uid"], "date": r["date"], "type": r["type"],
             "company_uid": r["company_uid"], "details": r["details"],
             "amount": r["amount"], "updated_at": r["updated_at"]}
            for r in conn.execute(
                "SELECT a.uid, a.date, a.type, c.uid AS company_uid, a.details, "
                "a.amount, a.updated_at "
                "FROM account a JOIN companies c ON a.company = c.refno"
            )
            if r["uid"] not in client_a or (r["updated_at"] or "") > client_a[r["uid"]]
        ]
        out_tombstones = [
            {"uid": r["uid"], "tbl": r["tbl"], "deleted_at": r["deleted_at"]}
            for r in conn.execute("SELECT uid, tbl, deleted_at FROM tombstones")
            if r["uid"] not in client_t
        ]

        balance = conn.execute(
            "SELECT COALESCE(SUM(CASE WHEN type='Receipt' THEN amount ELSE -amount END), 0) "
            "FROM account"
        ).fetchone()[0]
    finally:
        conn.close()

    return jsonify(
        applied=applied,
        backup=os.path.basename(backup_path),
        duplicates=duplicates,
        balance=balance,
        companies=out_companies,
        account=out_account,
        tombstones=out_tombstones,
    )


def tailscale_ip():
    try:
        out = subprocess.check_output(["tailscale", "ip", "-4"], text=True).strip()
        return out.splitlines()[0].strip() or None
    except Exception:
        return None


if __name__ == "__main__":
    ensure_schema()
    host = os.environ.get("SYNC_HOST") or tailscale_ip() or "0.0.0.0"
    print(f"prepaid-card sync server on http://{host}:{PORT}")
    print(f"token: {TOKEN}")
    if host == "0.0.0.0":
        print("warning: no Tailscale IP found; binding all interfaces", file=sys.stderr)
    app.run(host=host, port=PORT)
