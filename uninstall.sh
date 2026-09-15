#!/usr/bin/env bash
# =============================================================================
#  GKOne Prepaid Card — uninstaller
#
#  Removes the gk launcher, the application-menu entry, the sync service
#  (if enabled), and the installed program files from ~/.local/bin.
#
#  Your database (and sync token) are kept by default. Pass --purge-data
#  to delete those too — this cannot be undone.
#
#  Usage:
#     bash uninstall.sh               # remove program, keep your data
#     bash uninstall.sh --purge-data  # remove everything, including the database
# =============================================================================
set -euo pipefail

G='\033[0;32m'; Y='\033[1;33m'; R='\033[0;31m'; C='\033[0;36m'; W='\033[1m'; N='\033[0m'
ok()   { echo -e "${G}  ✓${N} $*"; }
info() { echo -e "${C}  ℹ${N} $*"; }
warn() { echo -e "${Y}  ⚠${N} $*"; }

BIN_DIR="$HOME/.local/bin"
APP_DIR="$BIN_DIR/prepaid-card"
APPMENU="$HOME/.local/share/applications/gkone.desktop"
UNIT="$HOME/.config/systemd/user/prepaid-sync.service"
PURGE=0

for arg in "$@"; do
    case "$arg" in
        --purge-data) PURGE=1 ;;
        -h|--help) sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $arg (see --help)" >&2; exit 1 ;;
    esac
done

echo -e "\n${W}  GKOne Prepaid Card — uninstaller${N}"
echo    "  ───────────────────────────────────────"

# ── 1. Sync receiver (systemd service, if enabled) ──────────────────────────
if command -v systemctl >/dev/null 2>&1 && systemctl --user is-enabled prepaid-sync >/dev/null 2>&1; then
    systemctl --user disable --now prepaid-sync 2>/dev/null || true
    ok "Disabled the prepaid-sync systemd service"
fi
if [[ -f "$UNIT" ]]; then
    rm -f "$UNIT"
    command -v systemctl >/dev/null 2>&1 && systemctl --user daemon-reload 2>/dev/null || true
    ok "Removed systemd unit"
fi
pgrep -f "python.*sync_server\.py" >/dev/null 2>&1 && \
    pkill -f "python.*sync_server\.py" 2>/dev/null && info "Stopped the running sync receiver"

# ── 2. Application-menu entry ───────────────────────────────────────────────
if [[ -f "$APPMENU" ]]; then
    rm -f "$APPMENU"
    command -v update-desktop-database >/dev/null 2>&1 && \
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null || true
    ok "Removed application-menu entry"
else
    info "No application-menu entry found"
fi

# ── 3. Launcher ──────────────────────────────────────────────────────────────
if [[ -f "$BIN_DIR/gk" ]]; then
    rm -f "$BIN_DIR/gk"
    ok "Removed launcher: $BIN_DIR/gk"
else
    info "No launcher found at $BIN_DIR/gk"
fi

# ── 4. Application files ────────────────────────────────────────────────────
if [[ ! -d "$APP_DIR" ]]; then
    info "Nothing installed at $APP_DIR"
elif [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$APP_DIR"
    ok "Removed $APP_DIR, including your database"
else
    find "$APP_DIR" -mindepth 1 ! -name 'prepaid_card.db' ! -name '.sync_token' -delete 2>/dev/null || true
    ok "Removed program files from $APP_DIR"
    [[ -f "$APP_DIR/prepaid_card.db" ]] && warn "Database kept: $APP_DIR/prepaid_card.db"
    [[ -f "$APP_DIR/.sync_token" ]] && warn "Sync token kept: $APP_DIR/.sync_token"
fi

echo ""
echo -e "${G}${W}  Uninstall complete.${N}"
if [[ "$PURGE" -eq 0 && -d "$APP_DIR" ]]; then
    echo -e "  Your data is still at ${C}$APP_DIR${N} — delete it yourself, or re-run with ${Y}--purge-data${N}."
fi
echo -e "  The shared virtual environment ${C}~/mybankenv${N} was left in place (other tools may use it)."
echo ""
