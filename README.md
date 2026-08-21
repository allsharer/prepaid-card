# GKOne Prepaid Card

A personal account tracker for the GKOne Prepaid Card. Available as both a terminal app and a web app, sharing the same SQLite database so both can be used side by side.

## Features

- Record payments and receipts against named payees and payers
- Running balance calculated across all transactions
- Filter and report transactions by date range, type, or company
- Add and remove payees/payers (removal blocked if linked to transactions)
- Terminal UI with arrow-key navigation and Escape to go back
- Web UI auto-closes the Flask server when the browser tab is shut

## Requirements

- Python 3.x
- A virtual environment at `~/mybankenv` with the following packages:
  - `flask`
  - `rich`
  - `questionary`

Install dependencies:

```bash
python -m venv ~/mybankenv
source ~/mybankenv/bin/activate
pip install flask rich questionary
```

## Usage

The `gk` launcher script must be on your PATH (e.g. in `~/bin/`):

```bash
gk            # terminal app
gk --web      # web app (opens browser automatically, runs on port 5000)
gk --help     # show usage
```

## Database

The database (`prepaid_card.db`) is created automatically on first run. To seed it with sample data:

```bash
sqlite3 prepaid_card.db < initial_data.sql
```

## File Overview

| File | Purpose |
|---|---|
| `prepaid_tracker.py` | Terminal app (rich + questionary) |
| `app.py` | Flask web app |
| `templates/` | Jinja2 HTML templates (Bootstrap 5) |
| `prepaid_card.db` | SQLite database (not in repo) |
| `initial_data.sql` | Sample companies and transactions |
| `gk` | Main launcher script |
| `runapp` | Activates venv and runs the terminal app |
| `runweb` | Activates venv and runs the web app |
