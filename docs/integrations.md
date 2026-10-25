# Integration configuration

The Shopify and Odoo adapters are application code; no provider account or credentials are included.
Use your own isolated staging accounts and populate the blank fields in `.env.example` locally.

Shopify requires a store domain, API version, access configuration and location mapping.
Odoo requires a base URL, database, version and least-privilege integration account.
Customer/product/unit mappings must be created explicitly for the target environment.
An empty approved market-pair list disables market imports.

Keep `SHOPIFY_FULFILLMENT_ENABLED`, `ODOO_COMPLETION_ENABLED` and scheduled import switches
disabled until configuration has been verified with synthetic records. Tests mock provider transports.
No provider credentials are needed for automated tests. Never point tests at live accounts.
