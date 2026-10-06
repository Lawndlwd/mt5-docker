#!/bin/sh
# Runs once per deploy. Only files whose content changed get rewritten, so the VM's
# supervisor restarts the workers only when something it runs actually changed.
set -eu

rsync -rlc --delete --chmod=D755,F644 --exclude __pycache__ --exclude /settings/ /src/ /shared/

mkdir -p /shared/settings
tmp=/shared/settings/.settings.env.tmp
printf 'MT5_INSTANCES=%s\nWORKER_BASE_PORT=%s\nLOG_LEVEL=%s\n' \
    "$MT5_INSTANCES" "$WORKER_BASE_PORT" "$LOG_LEVEL" > "$tmp"
chmod 644 "$tmp"
if cmp -s "$tmp" /shared/settings/settings.env; then rm "$tmp"; else mv "$tmp" /shared/settings/settings.env; fi

echo "Synced to /shared:"
find /shared -type f -not -path '*/__pycache__/*' | sort
