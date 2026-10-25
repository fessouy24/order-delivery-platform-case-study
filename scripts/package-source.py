"""Create a credential-audited ZIP from the staged Git tree; does not publish anything."""

import re
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
version = (root / "VERSION").read_text().strip()
if not re.fullmatch(r"[0-9][A-Za-z0-9.-]+", version):
    raise SystemExit("Invalid release version")
subprocess.run([sys.executable, str(root / "scripts/audit-source.py")], cwd=root, check=True)
git = ["git", "-c", f"safe.directory={root.as_posix()}"]
tree = subprocess.check_output(git + ["write-tree"], cwd=root, text=True).strip()
folder = root / "outputs" / "release"
folder.mkdir(parents=True, exist_ok=True)
output = folder / f"operations-platform-public-source-{version}.zip"
with output.open("wb") as stream:
    subprocess.run(
        git + ["archive", "--format=zip", "--prefix=operations-platform/", tree],
        cwd=root,
        stdout=stream,
        check=True,
    )
print(f"Public source archive: {output}")
print("Archive contains the staged source only. Review it before publishing; never include live data.")
