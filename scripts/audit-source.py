"""Audit staged source names and known local secret values without printing secrets."""

import fnmatch
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ALLOWED_ENV = {".env.example", ".env.push.example", ".env.production.example"}
PRIVATE_PARTS = {
    ".local-runtime",
    ".backups",
    "outputs",
    ".venv",
    "node_modules",
    ".gradle",
    ".git",
}
PRIVATE_PATTERNS = (
    "*.dump",
    "*.sqlite3",
    "*.keystore",
    "*.jks",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.log",
    "*.xlsx",
    "*.xlsm",
    "*.docx",
    "*.pdf",
)


def local_secret_values():
    values = set()
    for path in ROOT.glob(".env*"):
        if path.name in ALLOWED_ENV or not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.partition("=")
            if (
                sep
                and not key.lstrip().startswith("#")
                and re.search(r"SECRET|PASSWORD|API_KEY|ACCESS_TOKEN|CLIENT_ID|ODOO_LOGIN", key)
            ):
                value = value.strip().strip("\"'")
                if len(value) >= 8:
                    values.add(value)
    accounts = ROOT / ".trial-accounts.json"
    if accounts.exists():
        for account in json.loads(accounts.read_text(encoding="utf-8-sig")).values():
            if isinstance(account, dict) and len(account.get("password", "")) >= 8:
                values.add(account["password"])
    return values


def main():
    process = subprocess.run(
        ["git", "-c", f"safe.directory={ROOT.as_posix()}", "ls-files", "--cached", "-z"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    files = [name for name in process.stdout.decode("utf-8").split("\0") if name]
    if not files:
        print("No staged/tracked files. Stage the intended source, then run this audit again.")
        return 1
    secrets = local_secret_values()
    findings = []
    for name in files:
        parts = Path(name).parts
        forbidden = any(part in PRIVATE_PARTS for part in parts) or name.startswith(
            ("backend/media/", "docs/reference-data/")
        )
        forbidden |= (
            Path(name).name == ".trial-accounts.json" or Path(name).name == "google-services.json"
        )
        forbidden |= Path(name).name.startswith(".env") and name not in ALLOWED_ENV
        forbidden |= any(fnmatch.fnmatch(name.lower(), pattern) for pattern in PRIVATE_PATTERNS)
        if forbidden:
            findings.append((name, "private file type/path"))
            continue
        try:
            content = subprocess.check_output(
                ["git", "-c", f"safe.directory={ROOT.as_posix()}", "show", f":{name}"],
                cwd=ROOT,
            ).decode("utf-8")
        except UnicodeDecodeError:
            continue
        if any(value in content for value in secrets):
            findings.append((name, "known local credential or private login"))
        if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
            findings.append((name, "private key material"))
        if re.search(r"\bshp(?:at|ss|ca|pa)_[A-Za-z0-9]{24,}\b", content):
            findings.append((name, "provider token pattern"))
        if re.search(
            r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}"
            r"|AIza[A-Za-z0-9_-]{30,}|AKIA[0-9A-Z]{16})\b", content
        ):
            findings.append((name, "credential pattern"))
    for name, reason in findings:
        print(f"BLOCK: {name}: {reason}")
    if findings:
        print(f"FAIL: {len(findings)} finding(s). No credential contents printed.")
        return 1
    print(
        f"PASS: {len(files)} source files; no blocked paths, known local credentials or provider token patterns detected."
    )
    print(
        "Review staged changes manually too. This does not prove absence of every possible secret."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
