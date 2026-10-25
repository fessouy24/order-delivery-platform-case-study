# Sanitized source verification

Checked locally on 2026-10-25:

- Django system check: passed.
- Migration consistency (`makemigrations --check --dry-run`): no changes detected.
- Portable backend suite with `config.settings_test`: **283 passed, 2 skipped**.
- Ruff lint and formatting checks: passed.
- Frontend frozen-lockfile installation and TypeScript/Vite production build: passed.
- Staged-source audit and targeted scan for credential patterns, private paths,
  company identifiers, provider endpoints and contact data: reviewed.
- Git whitespace check: passed.

The two skipped tests require PostgreSQL row-lock/concurrency behavior; SQLite does
not validate that behavior. Docker was unavailable locally, so PostgreSQL checks
are defined in GitHub Actions rather than claimed as locally verified.
Android and Windows release builds have not been rerun for this sanitized edition.
No live Shopify/Odoo calls, company-data imports or deployments were performed.

These checks do not certify production readiness or guarantee the absence of every
possible sensitive value. Use an isolated environment and review your own configuration.
