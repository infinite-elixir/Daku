#!/usr/bin/env bash
# Daku installer for Linux (systemd --user).
#
# Idempotent: re-running it never duplicates the unit, overwrites config.json or touches
# the database. It never installs OS packages for you; it prints the exact command.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${DAKU_CONFIG_DIR:-$HOME/.config/daku}"
DATA_DIR="${DAKU_DATA_DIR:-$HOME/.local/share/daku}"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_FILE="$UNIT_DIR/daku.service"
CONFIG_FILE="$CONFIG_DIR/config.json"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarn:\033[0m %s\n' "$*" >&2; }

[ "$(uname -s)" = "Linux" ] || {
  warn "This installer targets Linux. Use install.ps1 on Windows."
  exit 1
}

say "Checking dependencies"
missing=()
for bin in uv ffmpeg ffprobe aria2c qbittorrent-nox; do
  command -v "$bin" >/dev/null 2>&1 || missing+=("$bin")
done
if ((${#missing[@]})); then
  warn "Missing: ${missing[*]}"
  warn "Install them first, for example:"
  warn "  sudo apt install ffmpeg aria2 qbittorrent-nox"
  warn "  curl -LsSf https://astral.sh/uv/install.sh | sh"
  warn "Daku still starts in a degraded state; run 'uv run daku doctor' to see what is missing."
fi
command -v uv >/dev/null 2>&1 || {
  warn "uv is required to create the virtual environment."
  exit 1
}
command -v mpv >/dev/null 2>&1 || warn "mpv not found: launching files will be unavailable."

say "Creating directories"
mkdir -p "$CONFIG_DIR" "$DATA_DIR" "$DATA_DIR/logs"
mkdir -p "$HOME/Downloads/Daku" "$HOME/Media/Movies" "$HOME/Media/TV" "$HOME/Media/Anime"
chmod 700 "$CONFIG_DIR" "$DATA_DIR"

say "Installing Python dependencies"
if [[ -f "$ROOT/uv.lock" ]]; then
  uv sync --frozen --project "$ROOT"
else
  uv sync --project "$ROOT"
fi

if command -v pnpm >/dev/null 2>&1; then
  say "Building the web UI"
  pnpm -C "$ROOT/web" install --frozen-lockfile
  pnpm -C "$ROOT/web" build
elif [[ -f "$ROOT/src/daku/webdist/index.html" ]]; then
  warn "pnpm not found; using the shipped web UI in src/daku/webdist."
else
  warn "pnpm not found and no shipped web UI exists; the browser will show a placeholder."
  warn "Install Node and pnpm, then run: pnpm -C web install && pnpm -C web build"
fi

if [[ ! -f "$CONFIG_FILE" ]]; then
  say "Writing $CONFIG_FILE"
  cat >"$CONFIG_FILE" <<JSON
{
  "host": "0.0.0.0",
  "port": 8420,
  "downloads_dir": "$HOME/Downloads/Daku",
  "movies_dir": "$HOME/Media/Movies",
  "tv_dir": "$HOME/Media/TV",
  "anime_dir": "$HOME/Media/Anime",
  "organize_mode": "hardlink",
  "qbt_url": "http://127.0.0.1:8080",
  "qbt_user": "admin",
  "aria2_rpc": "http://127.0.0.1:6800/jsonrpc",
  "mpv_enabled": true
}
JSON
  chmod 600 "$CONFIG_FILE"
else
  say "Keeping the existing $CONFIG_FILE"
fi

if command -v systemctl >/dev/null 2>&1; then
  say "Installing the systemd user unit"
  mkdir -p "$UNIT_DIR"
  cat >"$UNIT_FILE" <<UNIT
[Unit]
Description=Daku media librarian
Documentation=https://github.com/example/daku
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/daku
Environment=PYTHONUNBUFFERED=1
# Uncomment if mpv should open a window from a non-graphical login session.
# Environment=DISPLAY=:0
# Environment=WAYLAND_DISPLAY=wayland-0
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
UNIT
  systemctl --user daemon-reload
  systemctl --user enable --now daku
  if command -v loginctl >/dev/null 2>&1; then
    if ! loginctl show-user "$USER" -p Linger 2>/dev/null | grep -q 'Linger=yes'; then
      loginctl enable-linger "$USER" 2>/dev/null \
        || warn "Could not enable linger; run 'sudo loginctl enable-linger $USER' for boot start."
    fi
  fi
  say "Service status:"
  systemctl --user --no-pager --lines=0 status daku || true
else
  warn "systemctl not found; start Daku with: $ROOT/.venv/bin/daku"
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
[[ -n "$LAN_IP" ]] || LAN_IP="<host-ip>"

cat <<EOF

Daku is installed.

  Web UI      http://localhost:8420
  LAN         http://$LAN_IP:8420
  Config      $CONFIG_FILE
  Secrets     $CONFIG_DIR/secrets.json
  Data        $DATA_DIR
  Downloads   $HOME/Downloads/Daku
  Logs        journalctl --user -u daku -f

Put the qBittorrent password in secrets.json, then run:
  $ROOT/.venv/bin/daku doctor

WARNING: Daku has no authentication. Keep it on a trusted private network and never
port-forward it or expose port 8420 to the internet.
EOF
