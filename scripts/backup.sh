#!/bin/sh
set -eu
STAMP=$(date +%Y%m%d_%H%M%S)
FILE="/backups/cft_inventory_${STAMP}.dump"
export PGPASSWORD="${POSTGRES_PASSWORD}"
pg_dump -h "${POSTGRES_HOST:-db}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Fc -f "$FILE"
SIZE=$(stat -c %s "$FILE" 2>/dev/null || echo 0)
RETENTION=$(psql -h "${POSTGRES_HOST:-db}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -Atqc "SELECT COALESCE((SELECT value FROM app_settings WHERE key='backup_retention_days'),'${BACKUP_RETENTION_DAYS:-30}')" 2>/dev/null || echo "${BACKUP_RETENTION_DAYS:-30}")
find /backups -type f -name 'cft_inventory_*.dump' -mtime +"$RETENTION" -delete || true
psql -h "${POSTGRES_HOST:-db}" -U "${POSTGRES_USER}" -d "${POSTGRES_DB}" -c "INSERT INTO backup_operations(operation,filename,status,size_bytes,message) VALUES ('backup','$(basename "$FILE")','success',$SIZE,'Scheduled backup');" >/dev/null 2>&1 || true
echo "Backup created: $FILE ($SIZE bytes)"
