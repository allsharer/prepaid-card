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
  - `prompt_toolkit`

`gkone --install` creates this venv and installs these packages automatically
— the steps below are only needed for manual/offline setup.

```bash
python -m venv ~/mybankenv
source ~/mybankenv/bin/activate
pip install flask rich questionary prompt_toolkit
```

## Install

From this checkout:

```bash
./gkone --install              # install (or upgrade) to ~/.local/bin/prepaid-card
./gkone --install --with-sync  # also enable the Android sync receiver as a systemd service
./gkone --help
```

Sets up the venv above, copies the app into `~/.local/bin/prepaid-card`, puts
`gkone` on your PATH (`~/.local/bin/gkone`), and adds a **GKOne** entry to
your application menu. Safe to re-run to upgrade — your database is never
overwritten. On first install it carries over the database and sync token
from this checkout, if present.

## Uninstall

Once installed, `gkone` is on your PATH:

```bash
gkone --uninstall               # remove the program, keep your database
gkone --uninstall --purge-data  # remove everything, including the database
```

## Usage

Once installed, `gkone` is on your PATH:

```bash
gkone            # terminal app
gkone --web      # web app (opens browser automatically, runs on port 5000)
gkone --help     # show usage
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
| `gkone` | Main launcher / installer script (`--web`, `--install`, `--uninstall`) |
| `runapp` | Activates venv and runs the terminal app |
| `runweb` | Activates venv and runs the web app |
| `sync_server.py` | Android sync receiver (see below) |
| `sync_schema.py` | Additive DB migration adding sync columns/triggers |
| `sync-server` | Shell launcher for `sync_server.py` |
| `prepaid-sync.service` | systemd user unit for an always-on sync receiver |

## Android app sync

`sync_server.py` is a small receiver the [Android app](../android/) pushes to for
two-way sync of this same `prepaid_card.db`. Run `sync_schema.py` once (adds `uid` /
`updated_at` columns, a `tombstones` table, and triggers — the web and terminal apps
are untouched), then run `./sync-server` (binds this host's Tailscale IP, port 8765;
token in `.sync_token`). Or install `prepaid-sync.service` as a `systemctl --user`
unit. Merge is last-write-wins per row by `uid`; deletes propagate as tombstones.
