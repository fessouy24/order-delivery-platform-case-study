# Public source edition

This copy was prepared for public portfolio review with the author's authorization.
It retains the application logic, tests, API adapters and deployment templates.

Removed or replaced:

- Company names, app identifiers, logos and branded icons.
- Operational branch directory and geographic links, replaced by three fictional records.
- Internal rollout notes, acceptance reports and company-specific runbooks.
- One-off repair, inspection and training scripts targeting specific business orders/accounts.
- Business names and order/customer/product identifiers in test fixtures, replaced with demo values.

No operational database, customer exports, private environment files, signing keys, Firebase
credentials or Git history are included. Environment examples contain placeholders;
test credentials are deliberately synthetic and unsuitable for deployment.

The generic OPSDEMO namespace is independent of the original installed applications.
Do not use this sanitized version as an in-place replacement for an existing deployment:
database names, package IDs, storage keys and external reference prefixes have changed.
Use a fresh environment and explicitly configure your own provider mappings.

Automated scans supplement manual review and cannot guarantee that every possible
sensitive value has been identified. Review future changes before publication.
