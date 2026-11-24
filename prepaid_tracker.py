import os
import sqlite3
from datetime import datetime
import sys

import backup

import questionary
from questionary import Choice, Style as QStyle
from prompt_toolkit.key_binding import KeyBindings, merge_key_bindings
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich import box

console = Console()
DB = 'prepaid_card.db'

Q_STYLE = QStyle([
    ('qmark',       'fg:cyan bold'),
    ('question',    'bold'),
    ('answer',      'fg:cyan bold'),
    ('pointer',     'fg:yellow bold'),
    ('highlighted', 'fg:yellow bold'),
    ('selected',    'fg:cyan'),
    ('instruction', 'fg:gray italic'),
])

_ESC_INSTRUCTION = "(↑↓ to move, Enter to select, Esc to go back)"


def ask_select(message, choices, instruction=_ESC_INSTRUCTION):
    """questionary.select with Escape support — Escape returns None like Cancel."""
    q = questionary.select(message, choices=choices, style=Q_STYLE, instruction=instruction)

    kb = KeyBindings()

    @kb.add('escape')
    def _(event):
        event.app.exit(result=None)

    q.application.key_bindings = merge_key_bindings([q.application.key_bindings, kb])

    try:
        return q.unsafe_ask()
    except KeyboardInterrupt:
        return None


# ── Database ──────────────────────────────────────────────────────────────────

def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS companies
                 (refno INTEGER PRIMARY KEY AUTOINCREMENT,
                  name TEXT NOT NULL,
                  type TEXT NOT NULL CHECK(type IN ('Payee', 'Payer')))''')
    c.execute('''CREATE TABLE IF NOT EXISTS account
                 (refno INTEGER PRIMARY KEY AUTOINCREMENT,
                  date TEXT NOT NULL,
                  type TEXT NOT NULL CHECK(type IN ('Receipt', 'Payment')),
                  company INTEGER NOT NULL,
                  details TEXT,
                  amount REAL NOT NULL,
                  FOREIGN KEY(company) REFERENCES companies(refno))''')
    c.execute("SELECT COUNT(*) FROM account")
    if c.fetchone()[0] == 0:
        c.execute("INSERT INTO companies (name, type) VALUES (?, ?)", ('Opening Balance', 'Payer'))
        c.execute("INSERT INTO account (date, type, company, details, amount) VALUES (?, ?, ?, ?, ?)",
                  ('2025-07-01', 'Receipt', 1, 'Balance C/F', 26994.20))
    conn.commit()
    conn.close()


def get_balance():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT SUM(CASE WHEN type='Receipt' THEN amount ELSE -amount END) FROM account")
    balance = c.fetchone()[0] or 0.0
    conn.close()
    return balance


def list_payees():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT refno, name FROM companies WHERE type='Payee' ORDER BY name")
    result = c.fetchall()
    conn.close()
    return result


def list_payers():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT refno, name FROM companies WHERE type='Payer' ORDER BY name")
    result = c.fetchall()
    conn.close()
    return result


def get_company_name(refno):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT name FROM companies WHERE refno=?", (refno,))
    result = c.fetchone()
    conn.close()
    return result[0] if result else "Unknown"


# ── Formatting helpers ────────────────────────────────────────────────────────

def fmt(amount):
    return f"{float(amount):,.2f}"


def validate_date(date_str):
    if date_str == '0':
        return None
    for fmt_str in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(date_str, fmt_str).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return False


def validate_date_input(date_str):
    if not date_str:
        return None
    for fmt_str in ('%Y-%m-%d', '%Y%m%d'):
        try:
            return datetime.strptime(date_str, fmt_str).strftime('%Y-%m-%d')
        except ValueError:
            continue
    return None


def ok(msg):
    console.print(f"\n [green]✓[/green]  {msg}")


def err(msg):
    console.print(f"\n [red]✗[/red]  {msg}")


def warn(msg):
    console.print(f"\n [yellow]⚠[/yellow]  {msg}")


def prompt(label, hint="", default=""):
    hint_part = f" [dim]{hint}[/dim]" if hint else ""
    default_part = f" [dim](default: {default})[/dim]" if default else ""
    return console.input(f"  [cyan]{label}[/cyan]{hint_part}{default_part}: ")


def balance_text(balance):
    color = "green" if balance >= 0 else "red"
    return f"[bold {color}]{fmt(balance)}[/bold {color}]"


def show_header(title):
    console.clear()
    console.print(f"\n [bold blue]Prepaid Card Tracker[/bold blue] [dim]›[/dim] [bold]{title}[/bold]\n")


def print_cancel():
    console.print("\n [dim]Cancelled.[/dim]")


# ── Arrow-key selectors ───────────────────────────────────────────────────────

def select_transaction(title="Select a transaction", limit=20):
    """Show recent transactions as an arrow-key list. Returns refno or None."""
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""SELECT a.refno, a.date, a.type, a.company, a.details, a.amount
                 FROM account a ORDER BY a.date DESC, a.refno DESC LIMIT ?""", (limit,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        warn("No transactions found.")
        return None

    choices = []
    for refno, date, trans_type, company, details, amount in rows:
        company_name = get_company_name(company)
        marker = "R" if trans_type == "Receipt" else "P"
        detail_str = (details or "")[:18]
        label = f"#{refno:<4} {date}  [{marker}]  {company_name:<22} {detail_str:<20} {fmt(amount):>12}"
        choices.append(Choice(title=label, value=refno))

    choices.append(Choice(title="— Cancel —", value=None))

    return ask_select(title, choices)


def select_company(company_type, current=None):
    """Show companies as an arrow-key list. Returns refno or None."""
    rows = list_payees() if company_type == "Payee" else list_payers()

    if not rows:
        warn(f"No {company_type}s found. Add one from the main menu.")
        return None

    choices = []
    for refno, name in rows:
        marker = " ◀ current" if refno == current else ""
        choices.append(Choice(title=f"{name}{marker}", value=refno))

    choices.append(Choice(title="— Cancel —", value=None))

    return ask_select(f"Select {company_type}", choices)


def select_trans_type(current=None):
    """Arrow-key selector for transaction type. Returns 'Receipt', 'Payment', or None."""
    choices = [
        Choice(title=f"Receipt{'  ◀ current' if current == 'Receipt' else ''}", value="Receipt"),
        Choice(title=f"Payment{'  ◀ current' if current == 'Payment' else ''}", value="Payment"),
        Choice(title="— Cancel —", value=None),
    ]
    return ask_select("Select type", choices)


# ── Transaction display ───────────────────────────────────────────────────────

def make_transaction_table(transactions, show_balance_col=True):
    table = Table(box=box.SIMPLE_HEAD, show_header=True, header_style="bold dim",
                  padding=(0, 1), expand=False)
    table.add_column("Ref", style="dim", width=5, justify="right")
    table.add_column("Date", width=12)
    table.add_column("Type", width=9)
    table.add_column("Company", min_width=18)
    table.add_column("Details", min_width=16)
    table.add_column("Amount", justify="right", width=13)
    if show_balance_col:
        table.add_column("Balance", justify="right", width=13)

    running = 0.0
    for t in transactions:
        refno, date, trans_type, company, details, amount = t
        company_name = get_company_name(company)
        if trans_type == 'Receipt':
            running += amount
            type_label = "[green]Receipt[/green]"
            amount_str = f"[green]{fmt(amount)}[/green]"
        else:
            running -= amount
            type_label = "[red]Payment[/red]"
            amount_str = f"[red]{fmt(amount)}[/red]"

        bal_color = "green" if running >= 0 else "red"
        row = [str(refno), date, type_label, company_name, details or "", amount_str]
        if show_balance_col:
            row.append(f"[{bal_color}]{fmt(running)}[/{bal_color}]")
        table.add_row(*row)

    return table


def print_statement(sort_by=None, filter_value=None, date_from=None, date_to=None, show_balance=True):
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    query = "SELECT a.refno, a.date, a.type, a.company, a.details, a.amount FROM account a"
    params = []
    wheres = []

    if date_from and date_to:
        wheres.append("a.date BETWEEN ? AND ?")
        params.extend([date_from, date_to])
    elif date_from:
        wheres.append("a.date >= ?")
        params.append(date_from)
    elif date_to:
        wheres.append("a.date <= ?")
        params.append(date_to)

    if filter_value:
        if sort_by == 'code':
            wheres.append("a.company = ?")
            params.append(filter_value)
        elif sort_by == 'type':
            wheres.append("a.type = ?")
            params.append(filter_value)

    if wheres:
        query += " WHERE " + " AND ".join(wheres)
    query += " ORDER BY a.date ASC, a.refno ASC"

    c.execute(query, params)
    transactions = c.fetchall()
    conn.close()

    if not transactions:
        warn("No transactions found.")
        return

    console.print(make_transaction_table(transactions, show_balance_col=show_balance))


def show_transaction_summary(title, refno, date, trans_type, company, details, amount):
    color = "green" if trans_type == "Receipt" else "red"
    balance = get_balance()
    content = (
        f"  [dim]Ref[/dim]      {refno}\n"
        f"  [dim]Date[/dim]     {date}\n"
        f"  [dim]Type[/dim]     [{color}]{trans_type}[/{color}]\n"
        f"  [dim]Company[/dim]  {get_company_name(company)}\n"
        f"  [dim]Details[/dim]  {details or '—'}\n"
        f"  [dim]Amount[/dim]   [{color}]{fmt(amount)}[/{color}]\n"
        f"  [dim]Balance[/dim]  {balance_text(balance)}"
    )
    console.print(Panel(content, title=f"[green]✓ {title}[/green]", border_style="green",
                        padding=(1, 2)))


# ── Account functions ─────────────────────────────────────────────────────────

def add_account():
    show_header("Add Transaction")
    console.print("  [dim]Enter 0 at any prompt to cancel.[/dim]\n")

    # Date
    while True:
        today = datetime.now().strftime('%Y-%m-%d')
        raw = prompt("Date", "YYYY-MM-DD or YYYYMMDD", today).strip()
        if not raw:
            date = today; break
        elif raw == '0':
            print_cancel(); return
        date = validate_date(raw)
        if date:
            break
        elif date is False:
            err("Invalid date format. Please try again.")

    # Type
    console.print()
    trans_type = select_trans_type()
    if trans_type is None:
        print_cancel(); return

    # Company
    console.print()
    company_type = "Payee" if trans_type == "Payment" else "Payer"
    company = select_company(company_type)
    if company is None:
        print_cancel(); return

    # Details
    console.print()
    details = prompt("Details", "optional").strip()
    if details == '0':
        print_cancel(); return

    # Amount
    amount = None
    while amount is None:
        raw = prompt("Amount").strip()
        if raw == '0':
            print_cancel(); return
        try:
            amount = round(float(raw), 2)
            if amount <= 0:
                err("Amount must be greater than zero.")
                amount = None
        except ValueError:
            err("Please enter a valid number.")

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO account (date, type, company, details, amount) VALUES (?,?,?,?,?)",
              (date, trans_type, company, details, amount))
    conn.commit()
    refno = c.lastrowid
    conn.close()

    show_transaction_summary("Transaction Saved", refno, date, trans_type, company, details, amount)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def edit_account():
    show_header("Edit Transaction")

    refno = select_transaction("Select transaction to edit")
    if refno is None:
        print_cancel(); return

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT date, type, company, details, amount FROM account WHERE refno=?", (refno,))
    row = c.fetchone()
    if not row:
        err(f"Transaction #{refno} not found.")
        conn.close(); return

    old_date, old_type, old_company, old_details, old_amount = row
    color = "green" if old_type == "Receipt" else "red"
    content = (
        f"  [dim]Date[/dim]     {old_date}\n"
        f"  [dim]Type[/dim]     [{color}]{old_type}[/{color}]\n"
        f"  [dim]Company[/dim]  {get_company_name(old_company)}\n"
        f"  [dim]Details[/dim]  {old_details or '—'}\n"
        f"  [dim]Amount[/dim]   [{color}]{fmt(old_amount)}[/{color}]"
    )
    console.print(Panel(content, title=f"[bold]Transaction #{refno}[/bold]",
                        border_style="blue", padding=(1, 2)))
    console.print("  [dim]Leave blank to keep current value, 0 to cancel.[/dim]\n")

    # Date
    while True:
        raw = prompt("Date", "YYYY-MM-DD or YYYYMMDD", old_date).strip()
        if raw == '0':
            print_cancel(); conn.close(); return
        elif not raw:
            date = old_date; break
        validated = validate_date(raw)
        if validated:
            date = validated; break
        elif validated is False:
            err("Invalid date format.")

    # Type
    console.print()
    trans_type = select_trans_type(current=old_type)
    if trans_type is None:
        print_cancel(); conn.close(); return

    # Company
    console.print()
    expected_type = "Payee" if trans_type == "Payment" else "Payer"
    company = select_company(expected_type, current=old_company)
    if company is None:
        print_cancel(); conn.close(); return

    # Details
    console.print()
    raw = prompt("Details", f"blank to keep: {old_details or '—'}").strip()
    if raw == '0':
        print_cancel(); conn.close(); return
    details = raw if raw else old_details

    # Amount
    amount = None
    while amount is None:
        raw = prompt("Amount", f"blank to keep: {fmt(old_amount)}").strip()
        if raw == '0':
            print_cancel(); conn.close(); return
        elif not raw:
            amount = old_amount
        else:
            try:
                amount = round(float(raw), 2)
                if amount <= 0:
                    err("Amount must be greater than zero.")
                    amount = None
            except ValueError:
                err("Please enter a valid number.")

    c.execute("UPDATE account SET date=?, type=?, company=?, details=?, amount=? WHERE refno=?",
              (date, trans_type, company, details, amount, refno))
    conn.commit()
    conn.close()

    show_transaction_summary("Transaction Updated", refno, date, trans_type, company, details, amount)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def delete_account():
    show_header("Delete Transaction")

    refno = select_transaction("Select transaction to delete")
    if refno is None:
        print_cancel(); return

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT date, type, company, details, amount FROM account WHERE refno=?", (refno,))
    row = c.fetchone()
    if not row:
        err(f"Transaction #{refno} not found.")
        conn.close(); return

    date, trans_type, company, details, amount = row
    color = "green" if trans_type == "Receipt" else "red"
    content = (
        f"  [dim]Ref[/dim]      {refno}\n"
        f"  [dim]Date[/dim]     {date}\n"
        f"  [dim]Type[/dim]     [{color}]{trans_type}[/{color}]\n"
        f"  [dim]Company[/dim]  {get_company_name(company)}\n"
        f"  [dim]Details[/dim]  {details or '—'}\n"
        f"  [dim]Amount[/dim]   [{color}]{fmt(amount)}[/{color}]"
    )
    console.print(Panel(content, title="[bold red]Transaction to Delete[/bold red]",
                        border_style="red", padding=(1, 2)))

    confirm = console.input("\n  [bold red]Delete this transaction? (y/N):[/bold red] ").strip().lower()
    if confirm == 'y':
        c.execute("DELETE FROM account WHERE refno=?", (refno,))
        conn.commit()
        ok(f"Transaction #{refno} deleted.  Balance: {balance_text(get_balance())}")
    else:
        print_cancel()

    conn.close()
    console.input("\n  [dim]Press Enter to continue...[/dim]")


# ── Company functions ─────────────────────────────────────────────────────────

def add_payee():
    show_header("Add Payee")
    name = prompt("Payee Name", "0 to cancel").strip()
    if name == '0':
        print_cancel(); return
    if not name:
        err("Name cannot be empty."); return
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO companies (name, type) VALUES (?, ?)", (name, 'Payee'))
    conn.commit()
    conn.close()
    ok(f'Payee [bold]"{name}"[/bold] added.')
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def add_payer():
    show_header("Add Payer")
    name = prompt("Payer Name", "0 to cancel").strip()
    if name == '0':
        print_cancel(); return
    if not name:
        err("Name cannot be empty."); return
    conn = sqlite3.connect(DB)
    conn.execute("INSERT INTO companies (name, type) VALUES (?, ?)", (name, 'Payer'))
    conn.commit()
    conn.close()
    ok(f'Payer [bold]"{name}"[/bold] added.')
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def remove_company(company_type):
    show_header(f"Remove {company_type}")
    rows = list_payees() if company_type == 'Payee' else list_payers()
    if not rows:
        warn(f"No {company_type}s found.")
        console.input("\n  [dim]Press Enter to continue...[/dim]"); return

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    choices = []
    for refno, name in rows:
        c.execute("SELECT COUNT(*) FROM account WHERE company=?", (refno,))
        tx_count = c.fetchone()[0]
        if tx_count:
            choices.append(Choice(
                title=name,
                value=refno,
                disabled=f"{tx_count} transaction{'s' if tx_count > 1 else ''}",
            ))
        else:
            choices.append(Choice(title=name, value=refno))
    conn.close()

    choices.append(Choice(title="— Cancel —", value=None))

    selected = ask_select(f"Select {company_type} to remove", choices)

    if selected is None:
        print_cancel()
        console.input("\n  [dim]Press Enter to continue...[/dim]"); return

    name = get_company_name(selected)
    confirm = console.input(
        f"\n  [bold red]Delete {company_type.lower()} \"{name}\"? (y/N):[/bold red] "
    ).strip().lower()
    if confirm == 'y':
        conn = sqlite3.connect(DB)
        conn.execute("DELETE FROM companies WHERE refno=?", (selected,))
        conn.commit()
        conn.close()
        ok(f'{company_type} [bold]"{name}"[/bold] deleted.')
    else:
        print_cancel()
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def payee_menu():
    while True:
        show_header("Payee")
        choice = ask_select("What would you like to do?", [
            Choice("Add Payee",    value='1'),
            Choice("Remove Payee", value='2'),
            Choice("Back",         value='0'),
        ])
        if choice == '1':
            add_payee()
        elif choice == '2':
            remove_company('Payee')
        elif choice == '0' or choice is None:
            break


def payer_menu():
    while True:
        show_header("Payer")
        choice = ask_select("What would you like to do?", [
            Choice("Add Payer",    value='1'),
            Choice("Remove Payer", value='2'),
            Choice("Back",         value='0'),
        ])
        if choice == '1':
            add_payer()
        elif choice == '2':
            remove_company('Payer')
        elif choice == '0' or choice is None:
            break


# ── Reports ───────────────────────────────────────────────────────────────────

def list_payees_report():
    show_header("Payees")
    rows = list_payees()
    if not rows:
        warn("No payees found.")
    else:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold dim", padding=(0, 1))
        table.add_column("Ref", justify="right", width=5, style="dim")
        table.add_column("Name")
        for refno, name in rows:
            table.add_row(str(refno), name)
        console.print(table)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def list_payers_report():
    show_header("Payers")
    rows = list_payers()
    if not rows:
        warn("No payers found.")
    else:
        table = Table(box=box.SIMPLE_HEAD, header_style="bold dim", padding=(0, 1))
        table.add_column("Ref", justify="right", width=5, style="dim")
        table.add_column("Name")
        for refno, name in rows:
            table.add_row(str(refno), name)
        console.print(table)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def report_by_company(company_type):
    show_header(f"Statement by {company_type}")
    company = select_company(company_type)
    if company is None:
        print_cancel()
        console.input("\n  [dim]Press Enter to continue...[/dim]"); return

    name = get_company_name(company)
    console.print(f"\n  [bold]{company_type}: {name}[/bold]")
    print_statement('code', company, show_balance=False)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def report_by_type():
    show_header("Statement by Type")
    trans_type = select_trans_type()
    if trans_type is None:
        print_cancel()
        console.input("\n  [dim]Press Enter to continue...[/dim]"); return

    show_header(f"{trans_type}s")
    print_statement('type', trans_type, show_balance=False)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def report_by_date():
    show_header("Statement by Date Range")

    while True:
        raw = prompt("From Date", "YYYY-MM-DD or YYYYMMDD, blank for none").strip()
        date_from = validate_date_input(raw) if raw else None
        if raw and not date_from:
            err("Invalid date format."); continue
        break

    while True:
        raw = prompt("To Date", "YYYY-MM-DD or YYYYMMDD, blank for none").strip()
        date_to = validate_date_input(raw) if raw else None
        if raw and not date_to:
            err("Invalid date format."); continue
        break

    console.print()
    print_statement('date', None, date_from, date_to, show_balance=False)
    console.input("\n  [dim]Press Enter to continue...[/dim]")


# ── Menus ─────────────────────────────────────────────────────────────────────

def main_menu():
    init_db()
    while True:
        console.clear()
        balance = get_balance()
        bal_color = "green" if balance >= 0 else "red"
        console.print(Panel(
            f"\n  [dim]Current Balance[/dim]\n\n"
            f"  [bold {bal_color}]{fmt(balance)}[/bold {bal_color}]\n",
            title="[bold]PREPAID CARD TRACKER[/bold]",
            border_style="blue",
            padding=(0, 4),
            width=40,
        ))
        console.print()

        choice = ask_select("Main Menu", [
            Choice("Record  —  Add · Edit · Delete transactions", value='1'),
            Choice("Payee   —  Add / Remove payees",              value='2'),
            Choice("Payer   —  Add / Remove payers",              value='3'),
            Choice("Reports —  View statements",                   value='4'),
            Choice("Backup  —  Take / Restore a backup",           value='5'),
            Choice("Exit",                                         value='0'),
        ])

        if choice == '1':
            account_menu()
        elif choice == '2':
            payee_menu()
        elif choice == '3':
            payer_menu()
        elif choice == '4':
            reports_menu()
        elif choice == '5':
            backup_menu()
        elif choice == '0' or choice is None:
            console.clear()
            console.print("\n  [dim]Goodbye.[/dim]\n")
            sys.exit()


def account_menu():
    while True:
        show_header("Record")
        choice = ask_select("What would you like to do?", [
            Choice("Add Transaction",    value='1'),
            Choice("Edit Transaction",   value='2'),
            Choice("Delete Transaction", value='3'),
            Choice("Back",               value='0'),
        ])

        if choice == '1':
            add_account()
        elif choice == '2':
            edit_account()
        elif choice == '3':
            delete_account()
        elif choice == '0' or choice is None:
            break


def reports_menu():
    while True:
        show_header("Reports")
        choice = ask_select("Select a report", [
            Choice("All Transactions  —  Full statement with running balance", value='1'),
            Choice("By Payee          —  Filter by payee",                     value='2'),
            Choice("By Payer          —  Filter by payer",                     value='3'),
            Choice("By Type           —  Receipts or payments only",           value='4'),
            Choice("By Date Range     —  Custom date range",                   value='5'),
            Choice("List Payees",                                               value='6'),
            Choice("List Payers",                                               value='7'),
            Choice("Back",                                                      value='0'),
        ])

        if choice == '1':
            show_header("All Transactions")
            print_statement(show_balance=True)
            console.input("\n  [dim]Press Enter to continue...[/dim]")
        elif choice == '2':
            report_by_company('Payee')
        elif choice == '3':
            report_by_company('Payer')
        elif choice == '4':
            report_by_type()
        elif choice == '5':
            report_by_date()
        elif choice == '6':
            list_payees_report()
        elif choice == '7':
            list_payers_report()
        elif choice == '0' or choice is None:
            break


# ── Backup / Restore ─────────────────────────────────────────────────────────

def _backup_label(b):
    when = b["when"].strftime("%d %b %Y  %H:%M")
    if b["count"] is None:
        detail = "[unreadable]"
    else:
        detail = f"{b['count']} transactions · balance {fmt(b['balance'])}"
    return f"{when}  {b['kind']:<17}  {detail}"


def take_backup():
    show_header("Take a Backup")
    try:
        path = backup.make_backup(db_path=os.path.abspath(DB))
        ok(f"Backup saved: [cyan]{os.path.basename(path)}[/cyan]")
        console.print(f"   [dim]in {backup.BACKUP_DIR}[/dim]")
    except Exception as e:
        err(f"Backup failed: {e}")
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def restore_backup_menu():
    show_header("Restore a Backup")
    backups = backup.list_backups()
    if not backups:
        warn("No backups yet — take one first.")
        console.input("\n  [dim]Press Enter to continue...[/dim]")
        return

    console.print(f"  Current balance: {balance_text(get_balance())}\n")
    choices = [Choice(title=_backup_label(b), value=b["name"]) for b in backups]
    choices.append(Choice(title="— Cancel —", value=None))
    name = ask_select("Restore which backup? (newest first)", choices)
    if name is None:
        print_cancel(); return
    b = next(x for x in backups if x["name"] == name)

    console.print(Panel(
        f"  [dim]Backup[/dim]   {b['name']}\n"
        f"  [dim]Taken[/dim]    {b['when'].strftime('%d %b %Y %H:%M:%S')}  ({b['kind']})\n"
        f"  [dim]Balance[/dim]  {fmt(b['balance']) if b['count'] is not None else '—'}\n\n"
        "  Everything recorded after this backup will be replaced.\n"
        "  The current data is saved first, so you can restore it again.\n"
        "  [yellow]Note:[/yellow] the phone keeps its own data — the next sync may\n"
        "  bring back transactions the phone still has.",
        title="[bold red]Restore Backup[/bold red]", border_style="red", padding=(1, 2)))

    confirm = console.input("\n  [bold red]Replace the current data with this backup? (y/N):[/bold red] ").strip().lower()
    if confirm != 'y':
        print_cancel()
    else:
        try:
            safety = backup.restore_backup(name, db_path=os.path.abspath(DB))
            ok(f"Restored {name}.  Balance: {balance_text(get_balance())}")
            console.print(f"   [dim]Previous data saved as {os.path.basename(safety)}[/dim]")
        except Exception as e:
            err(f"Restore failed: {e}")
    console.input("\n  [dim]Press Enter to continue...[/dim]")


def backup_menu():
    while True:
        show_header("Backup")
        choice = ask_select("What would you like to do?", [
            Choice("Take a Backup now",  value='1'),
            Choice("Restore a Backup",   value='2'),
            Choice("Back",               value='0'),
        ])
        if choice == '1':
            take_backup()
        elif choice == '2':
            restore_backup_menu()
        elif choice == '0' or choice is None:
            break


if __name__ == "__main__":
    main_menu()
