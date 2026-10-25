#!/usr/bin/env bash
# Suitable for hourly monitoring. Exit failure is actionable; no secrets are printed.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
python3 - <<'PY'
from datetime import datetime, timezone
from pathlib import Path
import shutil, sys
issues = []
free = shutil.disk_usage('.').free
total = shutil.disk_usage('.').total
if free / total < .20 or free < 5 * 1024**3:
    issues.append('Server storage is low: free space is below 20% or 5 GiB.')
p = Path('.backups/last-success')
try:
    last = datetime.fromisoformat(p.read_text().strip().replace('Z', '+00:00'))
    if (datetime.now(timezone.utc) - last).total_seconds() > 26 * 3600:
        issues.append('No successful off-site backup in the last 26 hours.')
except (OSError, ValueError):
    issues.append('No valid successful off-site backup record.')
for issue in issues:
    print(issue)
sys.exit(bool(issues))
PY
