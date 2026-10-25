# Security

Keep `.env` files, database backups, customer uploads, private keys, service-account JSON,
employee accounts and provider tokens outside version control. Only example environments
with placeholders are distributed. Generate unique credentials for each deployment.

Use HTTPS, explicit allowed hosts, least-privilege integration accounts and separate
staging infrastructure. Review roles, MFA, backups and external-write switches before deployment.
Odoo invoice creation remains draft-only; external writes require explicit configuration.

Run `python scripts/audit-source.py` against staged files and review the diff before publishing.
Do not put secrets or customer data in public issues. Report security concerns through
GitHub private vulnerability reporting if enabled, or contact the repository owner privately.
