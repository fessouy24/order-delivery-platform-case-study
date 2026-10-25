#!/usr/bin/env bash
# Run from cron on the VPS. Remote MUST be an rclone crypt remote, configured privately.
set -euo pipefail
umask 077
cd "$(dirname "${BASH_SOURCE[0]}")/.."
remote="${1:?Supply the configured encrypted off-site rclone remote path}"
command -v rclone >/dev/null
remote_name="${remote%%:*}"
rclone config dump | python3 -c 'import json,sys; config=json.load(sys.stdin); sys.exit(config.get(sys.argv[1], {}).get("type") != "crypt")' "$remote_name"
mkdir -p .backups
exec 9>.backups/backup.lock
flock -n 9 || exit 0
stamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump=".backups/opsdemo-${stamp}.dump"
docker compose --env-file .env.production -f compose.vps.yaml exec -T db \
    sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --format=custom --no-owner --no-acl' > "$dump.partial"
test -s "$dump.partial"
mv "$dump.partial" "$dump"
rclone copyto "$dump" "${remote%/}/opsdemo-${stamp}.dump"
# A completed encrypted upload must be downloadable and match its local source.
rclone cat "${remote%/}/opsdemo-${stamp}.dump" | cmp - "$dump"
rm -- "$dump"
# Product photos are stored outside PostgreSQL and need their own recovery copy.
media=".backups/opsdemo-${stamp}-media.tar.gz"
docker compose --env-file .env.production -f compose.vps.yaml exec -T api \
    tar -czf - -C /app/media . > "$media.partial"
test -s "$media.partial"
mv "$media.partial" "$media"
rclone copyto "$media" "${remote%/}/opsdemo-${stamp}-media.tar.gz"
rclone cat "${remote%/}/opsdemo-${stamp}-media.tar.gz" | cmp - "$media"
rm -- "$media"
date -u +%FT%TZ > .backups/last-success
echo 'Database backup uploaded and verified.'
