#!/usr/bin/env bash
# Remove the Daku systemd user unit. Config, data, downloads and media are kept.
set -euo pipefail

UNIT_FILE="$HOME/.config/systemd/user/daku.service"

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user stop daku 2>/dev/null || true
  systemctl --user disable daku 2>/dev/null || true
fi

if [[ -f "$UNIT_FILE" ]]; then
  rm -f "$UNIT_FILE"
  printf 'Removed %s\n' "$UNIT_FILE"
else
  printf 'No unit at %s\n' "$UNIT_FILE"
fi

if command -v systemctl >/dev/null 2>&1; then
  systemctl --user daemon-reload || true
fi

cat <<'EOF'
Daku was uninstalled. Kept on disk:
  ~/.config/daku          config + secrets
  ~/.local/share/daku     database, logs, thumbnails
  ~/Downloads/Daku        download directory
  ~/Media/{Movies,TV,Anime}

Delete those folders yourself if you want a clean slate.
EOF
