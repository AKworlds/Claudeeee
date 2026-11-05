#!/usr/bin/env bash
# Consistent SQLite backup (uses the sqlite3 online backup API), keeps the newest 14.
# Run from anywhere; add to cron, e.g.:  15 3 * * * /opt/dashboard/deploy/backup.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STAMP="$(date +%F_%H%M%S)"
DEST_DIR="$ROOT/data/backups"
mkdir -p "$DEST_DIR"

docker compose -f "$ROOT/deploy/docker-compose.yml" exec -T app python -c '
import sqlite3, sys
src = sqlite3.connect("/srv/dashboard/data/dashboard.db")
dst = sqlite3.connect("/srv/dashboard/data/backups/dashboard-" + sys.argv[1] + ".db")
with dst:
    src.backup(dst)
dst.close(); src.close()
' "$STAMP"

# Retention: keep the 14 most recent backups.
ls -1t "$DEST_DIR"/dashboard-*.db 2>/dev/null | tail -n +15 | xargs -r rm -f --
echo "Backup written: $DEST_DIR/dashboard-$STAMP.db"
