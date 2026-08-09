#!/bin/bash
# Daily SQLite backup + uploads archive
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DATE=$(date +%Y%m%d)
mkdir -p "$ROOT/data/backups"
sqlite3 "$ROOT/data/VK_Pets_DB.db" ".backup '$ROOT/data/backups/VK_Pets_DB_$DATE.db'"
tar -czf "$ROOT/data/backups/uploads_$DATE.tar.gz" -C "$ROOT/data" uploads 2>/dev/null || true
echo "Backup done: $DATE"
