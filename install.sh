#!/usr/bin/env bash
# =============================================================================
#  GKOne Prepaid Card — installer
#
#  Installs the app into ~/.local/bin/prepaid-card, puts the `gk` launcher
#  on your PATH (~/.local/bin/gk), and adds an application-menu entry.
#  Safe to re-run to upgrade — your database is never overwritten.
#
#  Usage:
#     bash install.sh              # install / upgrade
#     bash install.sh --with-sync  # also enable the Android sync receiver
#                                   # as an always-on systemd user service
#
#  Requires Python 3 with the `venv` module (Debian/Ubuntu:
#  sudo apt install python3-venv). Uses the shared venv at ~/mybankenv.
# =============================================================================
set -euo pipefail

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; W='\033[1m'; N='\033[0m'
ok()   { echo -e "${G}  ✓${N} $*"; }
info() { echo -e "${C}  ℹ${N} $*"; }
warn() { echo -e "${Y}  ⚠${N} $*"; }
die()  { echo -e "${R}  ✗${N} $*" >&2; exit 1; }

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN_DIR="$HOME/.local/bin"
APP_DIR="$BIN_DIR/prepaid-card"
VENV="${MYBANKENV:-$HOME/mybankenv}"
APPMENU_DIR="$HOME/.local/share/applications"
WITH_SYNC=0

for arg in "$@"; do
    case "$arg" in
        --with-sync) WITH_SYNC=1 ;;
        -h|--help) sed -n '2,17p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "Unknown option: $arg (see --help)" ;;
    esac
done

echo -e "\n${W}  GKOne Prepaid Card — installer${N}"
echo    "  ─────────────────────────────────────"

# ── 1. Python + shared virtual environment ─────────────────────────────────
command -v python3 >/dev/null 2>&1 || die "python3 not found. Install Python 3 first."
if [[ ! -d "$VENV" ]]; then
    info "Creating virtual environment: $VENV"
    python3 -m venv "$VENV" || die "Could not create a venv. On Debian/Ubuntu: sudo apt install python3-venv"
fi
PYBIN="$VENV/bin/python"

if ! "$PYBIN" -m pip install --quiet flask rich questionary prompt_toolkit; then
    warn "Could not reach PyPI — checking what's already installed in $VENV"
fi
MISSING=""
for pkg in flask rich questionary prompt_toolkit; do
    "$PYBIN" -c "import $pkg" 2>/dev/null || MISSING="$MISSING $pkg"
done
[[ -z "$MISSING" ]] || die "Missing Python packages in $VENV:$MISSING (no network available to install them)"
ok "Virtual environment ready: $VENV"

# ── 2. Application files ────────────────────────────────────────────────────
mkdir -p "$APP_DIR"
for f in app.py prepaid_tracker.py sync_server.py sync_schema.py sync-server \
         runapp runweb initial_data.sql prepaid-sync.service README.md; do
    [[ -f "$HERE/$f" ]] && cp "$HERE/$f" "$APP_DIR/"
done
rm -rf "$APP_DIR/templates" "$APP_DIR/static"
cp -r "$HERE/templates" "$APP_DIR/templates"
cp -r "$HERE/static" "$APP_DIR/static"
chmod +x "$APP_DIR/sync-server" "$APP_DIR/runapp" "$APP_DIR/runweb" 2>/dev/null || true
ok "Application files: $APP_DIR"

# Carry over an existing sync token on first install only (keeps the Android
# app paired without re-registering it).
if [[ -f "$HERE/.sync_token" && ! -f "$APP_DIR/.sync_token" ]]; then
    cp "$HERE/.sync_token" "$APP_DIR/.sync_token"
    chmod 600 "$APP_DIR/.sync_token"
    info "Carried over existing sync token"
fi

# ── 3. Database — never overwritten once installed ─────────────────────────
if [[ -f "$APP_DIR/prepaid_card.db" ]]; then
    info "Existing installed database left untouched"
elif [[ -f "$HERE/prepaid_card.db" ]]; then
    cp "$HERE/prepaid_card.db" "$APP_DIR/prepaid_card.db"
    info "Copied your existing database into $APP_DIR"
else
    (cd "$APP_DIR" && "$PYBIN" -c "from prepaid_tracker import init_db; init_db()")
    info "Initialized a fresh empty database (seed with initial_data.sql if wanted)"
fi
"$PYBIN" "$APP_DIR/sync_schema.py" >/dev/null
ok "Database ready: $APP_DIR/prepaid_card.db"

# ── 4. gk launcher, on PATH ─────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
cp "$HERE/gk" "$BIN_DIR/gk"
chmod +x "$BIN_DIR/gk"
ok "Launcher: $BIN_DIR/gk"
case ":$PATH:" in
    *":$BIN_DIR:"*) ;;
    *) warn "$BIN_DIR is not on your PATH — add it in ~/.bashrc or ~/.profile" ;;
esac

# ── 5. Application-menu launcher (icon already provided by gkone.desktop) ──
mkdir -p "$APPMENU_DIR"
cat > "$APPMENU_DIR/gkone.desktop" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=GKOne
Comment=GKOne Prepaid Card
Exec="$BIN_DIR/gk" --web
Icon=accessories-calculator
Terminal=false
Categories=Office;Finance;
StartupNotify=false
EOF
chmod +x "$APPMENU_DIR/gkone.desktop"
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPMENU_DIR" 2>/dev/null || true
ok "Application-menu entry: GKOne"

# ── 6. Android sync receiver (optional, always-on) ─────────────────────────
if [[ "$WITH_SYNC" -eq 1 ]]; then
    UNIT_DIR="$HOME/.config/systemd/user"
    mkdir -p "$UNIT_DIR"
    sed "s|%h/.local/bin/prepaid-card|$APP_DIR|" "$APP_DIR/prepaid-sync.service" > "$UNIT_DIR/prepaid-sync.service"
    if command -v systemctl >/dev/null 2>&1; then
        systemctl --user daemon-reload
        systemctl --user enable --now prepaid-sync
        ok "Sync receiver enabled as a systemd user service"
    else
        warn "systemctl not found — service unit written but not enabled"
    fi
else
    info "Sync receiver not enabled (re-run with --with-sync, or see README)"
fi

echo ""
echo -e "${G}${W}  Installation complete.${N}"
echo -e "  ${W}To run:${N} find ${C}GKOne${N} in your applications menu, or from a terminal:"
echo -e "          ${Y}gk${N}          — terminal app"
echo -e "          ${Y}gk --web${N}    — web app (opens your browser)"
echo ""
echo -e "  Installed at :  ${C}$APP_DIR${N}"
echo -e "  Uninstall    :  ${Y}bash $HERE/uninstall.sh${N}"
echo ""
