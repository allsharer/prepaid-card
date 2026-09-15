import sqlite3
import os
import signal
import threading
import time
from datetime import datetime
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify

app = Flask(__name__)
app.secret_key = 'prepaid-card-tracker-key'

# ── Browser watchdog ──────────────────────────────────────────────────────────
# Two-path shutdown:
#   Fast path  — /closed (sendBeacon on pagehide): shuts down within ~1.5 s.
#                A 1.5 s grace period lets a new page's /ping cancel the timer
#                when the user navigates within the app rather than closing.
#   Fallback   — /ping heartbeat every 3 s. Timeout is 90 s so browser background
#                throttling (Chrome can drop timers to ~1/min) never false-fires.
#                Covers crashes / force-kills where pagehide never fires.

_last_ping = time.time()
_PING_TIMEOUT = 90  # seconds
_close_timer = None
_close_lock = threading.Lock()


def _kill_sync():
    """Stop the sibling sync receiver that gkone --web started, if any."""
    pid = os.environ.get('PREPAID_SYNC_PID')
    if not pid:
        return
    try:
        os.kill(int(pid), signal.SIGTERM)
    except (ValueError, ProcessLookupError, PermissionError):
        pass


def _do_shutdown():
    print("\nBrowser tab closed — shutting down.", flush=True)
    _kill_sync()
    os.kill(os.getpid(), signal.SIGINT)


def _watchdog():
    time.sleep(10)  # grace period so the browser has time to load the first page
    while True:
        time.sleep(5)
        if time.time() - _last_ping > _PING_TIMEOUT:
            print("\nNo browser activity detected — shutting down.", flush=True)
            _kill_sync()
            os.kill(os.getpid(), signal.SIGINT)
            break


if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
    threading.Thread(target=_watchdog, daemon=True).start()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'prepaid_card.db')


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_balance():
    conn = get_db()
    row = conn.execute(
        "SELECT SUM(CASE WHEN type='Receipt' THEN amount ELSE -amount END) FROM account"
    ).fetchone()
    conn.close()
    return row[0] or 0.0


def format_amount(amount):
    return f"{float(amount):,.2f}"


app.jinja_env.filters['fmt_amount'] = format_amount


def validate_date(date_str):
    if not date_str:
        return None
    for fmt in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(date_str, fmt).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return False


@app.route('/ping', methods=['POST'])
def ping():
    global _last_ping, _close_timer
    _last_ping = time.time()
    with _close_lock:
        if _close_timer is not None:
            _close_timer.cancel()
            _close_timer = None
    return '', 204


@app.route('/closed', methods=['POST'])
def closed():
    global _close_timer
    with _close_lock:
        if _close_timer is not None:
            _close_timer.cancel()
        _close_timer = threading.Timer(1.5, _do_shutdown)
        _close_timer.daemon = True
        _close_timer.start()
    return '', 204


# ── API ──────────────────────────────────────────────────────────────────────

@app.route('/api/companies/<company_type>')
def api_companies(company_type):
    if company_type not in ('Payee', 'Payer'):
        return jsonify([])
    conn = get_db()
    rows = conn.execute(
        "SELECT refno, name FROM companies WHERE type=? ORDER BY name", (company_type,)
    ).fetchall()
    conn.close()
    return jsonify([{'refno': r['refno'], 'name': r['name']} for r in rows])


# ── Dashboard ────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    conn = get_db()
    rows = conn.execute("""
        SELECT a.refno, a.date, a.type, c.name AS company, a.details, a.amount
        FROM account a
        JOIN companies c ON a.company = c.refno
        ORDER BY a.date DESC, a.refno DESC
        LIMIT 10
    """).fetchall()
    conn.close()
    return render_template('index.html', balance=get_balance(), transactions=rows)


# ── Transactions ─────────────────────────────────────────────────────────────

@app.route('/transactions/add', methods=['GET', 'POST'])
def add_transaction():
    if request.method == 'POST':
        date_raw = request.form.get('date', '').strip()
        trans_type = request.form.get('type', 'Payment')
        company = request.form.get('company', '').strip()
        details = request.form.get('details', '').strip()
        amount_raw = request.form.get('amount', '').strip()

        date = validate_date(date_raw) if date_raw else datetime.now().strftime('%Y-%m-%d')
        if date is False:
            flash('Invalid date format.', 'danger')
            return redirect(url_for('add_transaction'))

        try:
            company_id = int(company)
        except (ValueError, TypeError):
            flash('Please select a company.', 'danger')
            return redirect(url_for('add_transaction'))

        try:
            amount = round(float(amount_raw), 2)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('add_transaction'))

        conn = get_db()
        conn.execute(
            "INSERT INTO account (date, type, company, details, amount) VALUES (?,?,?,?,?)",
            (date, trans_type, company_id, details, amount)
        )
        conn.commit()
        conn.close()
        flash(f'Transaction added. Balance: {format_amount(get_balance())}', 'success')
        return redirect(url_for('index'))

    return render_template('transaction_form.html',
                           title='Add Transaction',
                           action=url_for('add_transaction'),
                           today=datetime.now().strftime('%Y-%m-%d'),
                           transaction=None)


@app.route('/transactions/<int:refno>/edit', methods=['GET', 'POST'])
def edit_transaction(refno):
    conn = get_db()

    if request.method == 'POST':
        date_raw = request.form.get('date', '').strip()
        trans_type = request.form.get('type', 'Payment')
        company = request.form.get('company', '').strip()
        details = request.form.get('details', '').strip()
        amount_raw = request.form.get('amount', '').strip()

        date = validate_date(date_raw)
        if not date:
            flash('Invalid date format.', 'danger')
            return redirect(url_for('edit_transaction', refno=refno))

        try:
            company_id = int(company)
        except (ValueError, TypeError):
            flash('Please select a company.', 'danger')
            return redirect(url_for('edit_transaction', refno=refno))

        try:
            amount = round(float(amount_raw), 2)
        except ValueError:
            flash('Invalid amount.', 'danger')
            return redirect(url_for('edit_transaction', refno=refno))

        conn.execute(
            "UPDATE account SET date=?, type=?, company=?, details=?, amount=? WHERE refno=?",
            (date, trans_type, company_id, details, amount, refno)
        )
        conn.commit()
        conn.close()
        flash(f'Transaction updated. Balance: {format_amount(get_balance())}', 'success')
        return redirect(url_for('index'))

    row = conn.execute("""
        SELECT a.refno, a.date, a.type, a.company, c.name AS company_name, a.details, a.amount
        FROM account a
        JOIN companies c ON a.company = c.refno
        WHERE a.refno=?
    """, (refno,)).fetchone()
    conn.close()

    if not row:
        flash('Transaction not found.', 'danger')
        return redirect(url_for('index'))

    return render_template('transaction_form.html',
                           title='Edit Transaction',
                           action=url_for('edit_transaction', refno=refno),
                           today=datetime.now().strftime('%Y-%m-%d'),
                           transaction=row)


@app.route('/transactions/<int:refno>/delete', methods=['POST'])
def delete_transaction(refno):
    conn = get_db()
    conn.execute("DELETE FROM account WHERE refno=?", (refno,))
    conn.commit()
    conn.close()
    flash(f'Transaction deleted. Balance: {format_amount(get_balance())}', 'success')
    return redirect(url_for('index'))


# ── Companies ─────────────────────────────────────────────────────────────────

@app.route('/companies')
def companies():
    conn = get_db()
    payees = conn.execute("""
        SELECT c.refno, c.name, COUNT(a.refno) AS tx_count
        FROM companies c
        LEFT JOIN account a ON a.company = c.refno
        WHERE c.type='Payee'
        GROUP BY c.refno, c.name
        ORDER BY c.name
    """).fetchall()
    payers = conn.execute("""
        SELECT c.refno, c.name, COUNT(a.refno) AS tx_count
        FROM companies c
        LEFT JOIN account a ON a.company = c.refno
        WHERE c.type='Payer'
        GROUP BY c.refno, c.name
        ORDER BY c.name
    """).fetchall()
    conn.close()
    return render_template('companies.html', payees=payees, payers=payers)


@app.route('/companies/<int:refno>/delete', methods=['POST'])
def delete_company(refno):
    conn = get_db()
    row = conn.execute("SELECT name, type FROM companies WHERE refno=?", (refno,)).fetchone()
    if not row:
        flash('Company not found.', 'danger')
        conn.close()
        return redirect(url_for('companies'))

    tx_count = conn.execute(
        "SELECT COUNT(*) FROM account WHERE company=?", (refno,)
    ).fetchone()[0]

    if tx_count:
        flash(f'Cannot delete "{row["name"]}" — it is used in {tx_count} transaction(s).', 'danger')
        conn.close()
        return redirect(url_for('companies'))

    conn.execute("DELETE FROM companies WHERE refno=?", (refno,))
    conn.commit()
    conn.close()
    flash(f'{row["type"]} "{row["name"]}" deleted.', 'success')
    return redirect(url_for('companies'))


@app.route('/companies/add', methods=['POST'])
def add_company():
    name = request.form.get('name', '').strip()
    company_type = request.form.get('company_type', 'Payee')

    if not name:
        flash('Name cannot be empty.', 'danger')
        return redirect(url_for('companies'))
    if company_type not in ('Payee', 'Payer'):
        flash('Invalid company type.', 'danger')
        return redirect(url_for('companies'))

    conn = get_db()
    conn.execute("INSERT INTO companies (name, type) VALUES (?,?)", (name, company_type))
    conn.commit()
    conn.close()
    flash(f'{company_type} "{name}" added.', 'success')
    return redirect(url_for('companies'))


# ── Reports ───────────────────────────────────────────────────────────────────

@app.route('/reports')
def reports():
    conn = get_db()
    payees = conn.execute(
        "SELECT refno, name FROM companies WHERE type='Payee' ORDER BY name"
    ).fetchall()
    payers = conn.execute(
        "SELECT refno, name FROM companies WHERE type='Payer' ORDER BY name"
    ).fetchall()

    # Build query from filters
    filter_type = request.args.get('filter_type', 'all')
    date_from = request.args.get('date_from', '').strip()
    date_to = request.args.get('date_to', '').strip()
    company_id = request.args.get('company_id', '').strip()
    trans_type = request.args.get('trans_type', '').strip()

    query = """
        SELECT a.refno, a.date, a.type, c.name AS company, a.details, a.amount
        FROM account a
        JOIN companies c ON a.company = c.refno
    """
    params = []
    wheres = []

    if date_from:
        d = validate_date(date_from)
        if d:
            wheres.append("a.date >= ?")
            params.append(d)
    if date_to:
        d = validate_date(date_to)
        if d:
            wheres.append("a.date <= ?")
            params.append(d)
    if company_id:
        try:
            wheres.append("a.company = ?")
            params.append(int(company_id))
        except ValueError:
            pass
    if trans_type in ('Receipt', 'Payment'):
        wheres.append("a.type = ?")
        params.append(trans_type)

    if wheres:
        query += " WHERE " + " AND ".join(wheres)
    query += " ORDER BY a.date ASC, a.refno ASC"

    transactions = conn.execute(query, params).fetchall()
    conn.close()

    # Compute running balance
    show_balance = not (company_id or trans_type)
    rows_with_balance = []
    running = 0.0
    for t in transactions:
        if t['type'] == 'Receipt':
            running += t['amount']
        else:
            running -= t['amount']
        rows_with_balance.append({
            'refno': t['refno'],
            'date': t['date'],
            'type': t['type'],
            'company': t['company'],
            'details': t['details'],
            'amount': t['amount'],
            'balance': running,
        })

    return render_template('reports.html',
                           payees=payees,
                           payers=payers,
                           transactions=rows_with_balance,
                           show_balance=show_balance,
                           date_from=date_from,
                           date_to=date_to,
                           company_id=company_id,
                           trans_type=trans_type,
                           total=running)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
