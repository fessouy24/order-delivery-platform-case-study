#!/bin/sh
set -eu
export PORT="${PORT:-10000}"
case "$PORT" in *[!0-9]*|'') echo 'Invalid web port' >&2; exit 1;; esac
python manage.py check --deploy --fail-level WARNING
python manage.py collectstatic --noinput
envsubst '${PORT}' < /etc/opsdemo/nginx.conf.template > /tmp/opsdemo-nginx.conf
nginx -t -c /tmp/opsdemo-nginx.conf
exec supervisord -c /etc/opsdemo/supervisor.conf
