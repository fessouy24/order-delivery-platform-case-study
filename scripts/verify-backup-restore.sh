#!/usr/bin/env bash
# Restore an off-site backup into a new isolated database; never overwrite the live one.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
object="${1:?Supply an encrypted rclone remote backup object}"
test_db="restore_check_$(date -u +%Y%m%d%H%M%S)_${RANDOM}"
dc=(docker compose --env-file .env.production -f compose.vps.yaml)
"${dc[@]}" exec -T db sh -c 'createdb -U "$POSTGRES_USER" "$1"' sh "$test_db"
cleanup() {
    "${dc[@]}" exec -T db sh -c 'dropdb -U "$POSTGRES_USER" "$1"' sh "$test_db"
}
trap cleanup EXIT
rclone cat "$object" | "${dc[@]}" exec -T db \
    sh -c 'pg_restore -U "$POSTGRES_USER" -d "$1" --no-owner --no-acl --exit-on-error' sh "$test_db"
"${dc[@]}" exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$1" -v ON_ERROR_STOP=1 -c "SELECT COUNT(*) AS restored_orders FROM operations_order;"' sh "$test_db"
echo 'Off-site database restore completed in an isolated database.'
