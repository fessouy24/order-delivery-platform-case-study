# Deployment template

The supplied Compose and proxy templates need environment-specific review before use.
Start with a fresh host and fresh secrets. `scripts/init-production.py --help` describes
generation of an isolated production configuration. `compose.vps.yaml` provides a Caddy
HTTPS entry point while keeping database, Redis and API traffic on internal networks.

Before enabling a deployment, verify DNS/TLS, allowed hosts and CSRF origins, role access,
MFA policy, persistent media, off-site encrypted backups and a full restore exercise.
Use least-privilege Shopify/Odoo accounts and validate mappings against staging data.
No production endpoint, certificate, database or credentials are bundled.

This public release has not been verified as a live deployment. Use the actual target
environment for acceptance testing and review the container definitions before starting services.
