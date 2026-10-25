#!/usr/bin/env bash
# Read-only production checks; never changes firewall or server access.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 - <<'PY'
import json, os, stat, subprocess
from pathlib import Path
p = Path('.env.production')
assert p.is_file(), 'Production environment is missing'
assert not stat.S_IMODE(p.stat().st_mode) & 0o077, 'Restrict .env.production to its owner (chmod 600)'
config = json.loads(subprocess.check_output(['docker','compose','--env-file',str(p),'-f','compose.vps.yaml','config','--format','json']))
services = config['services']
for name, service in services.items():
    assert name == 'https' or not service.get('ports'), f'{name} unexpectedly publishes a port'
env = services['api']['environment']
assert env.get('APP_ENV') == 'production', 'Production mode is required'
assert env.get('REQUIRE_STAFF_MFA') in {'true', 'false'}, 'Set the staff sign-in policy explicitly'
assert '*' not in env.get('DJANGO_ALLOWED_HOSTS',''), 'Wildcard hosts are forbidden'
assert len(env.get('DJANGO_SECRET_KEY','')) >= 50, 'Use a fresh strong application secret'
assert env.get('DJANGO_CSRF_TRUSTED_ORIGINS','').startswith('https://'), 'HTTPS CSRF origin is required'
print('Private service ports, production settings and secret-file permissions passed.')
PY
docker compose --env-file .env.production -f compose.vps.yaml exec -T api python manage.py check --deploy --fail-level WARNING
echo 'Also verify the DigitalOcean firewall, account MFA, SSH keys and off-site restore; these are external checks.'
