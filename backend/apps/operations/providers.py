"""Provider transport boundary. Credentials never enter API representations."""

import json
import os
import re
import time
import xmlrpc.client
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


class IntegrationError(Exception):
    pass


class TransientError(IntegrationError):
    pass


class UnknownCreateResult(IntegrationError):
    pass


def secret(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise IntegrationError(f"Configure {name} before enabling this integration.")
    return value


class NoProviderRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise IntegrationError(
            "Provider redirected the API request. Configure its canonical HTTPS endpoint."
        )


def request_json(url, payload, headers=None, form=False):
    raw = urlencode(payload).encode() if form else json.dumps(payload).encode()
    request = Request(
        url,
        data=raw,
        headers={
            "Content-Type": "application/x-www-form-urlencoded" if form else "application/json",
            **(headers or {}),
        },
    )
    try:
        with build_opener(NoProviderRedirects()).open(request, timeout=20) as response:
            return json.load(response)
    except HTTPError as exc:
        if exc.code in {429, 500, 502, 503, 504}:
            raise TransientError(f"Provider temporarily unavailable (HTTP {exc.code}).") from exc
        raise IntegrationError(
            f"Provider rejected the request (HTTP {exc.code}). Check access and mappings."
        ) from exc
    except (URLError, TimeoutError, OSError, ValueError) as exc:
        raise TransientError("Provider connection or response was unsuccessful.") from exc


class Shopify:
    def __init__(self):
        self.store = secret("SHOPIFY_SHOP_DOMAIN").lower()
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*\.myshopify\.com", self.store):
            raise IntegrationError("SHOPIFY_SHOP_DOMAIN must be a myshopify.com hostname.")
        self.version = secret("SHOPIFY_API_VERSION")
        if not re.fullmatch(r"\d{4}-(01|04|07|10)", self.version):
            raise IntegrationError("Configure a supported stable Shopify API version.")
        self.access_token = os.environ.get("SHOPIFY_ACCESS_TOKEN", "")
        self.expires_at = float("inf") if self.access_token else 0

    def token(self):
        if time.time() >= self.expires_at:
            result = request_json(
                f"https://{self.store}/admin/oauth/access_token",
                {
                    "grant_type": "client_credentials",
                    "client_id": secret("SHOPIFY_CLIENT_ID"),
                    "client_secret": secret("SHOPIFY_CLIENT_SECRET"),
                },
                form=True,
            )
            token = result.get("access_token") if isinstance(result, dict) else None
            expires = result.get("expires_in") if isinstance(result, dict) else None
            if (
                not isinstance(token, str)
                or not token.strip()
                or type(expires) is not int
                or expires <= 60
            ):
                raise TransientError("Shopify returned an invalid token response; retry later.")
            self.access_token = token
            self.expires_at = time.time() + expires - 60
        return self.access_token

    def graphql(self, query, variables):
        result = request_json(
            f"https://{self.store}/admin/api/{self.version}/graphql.json",
            {"query": query, "variables": variables},
            {"X-Shopify-Access-Token": self.token()},
        )
        if result.get("errors"):
            if any(
                error.get("extensions", {}).get("code") == "THROTTLED" for error in result["errors"]
            ):
                raise TransientError("Shopify query throttled; retry later.")
            raise IntegrationError(
                "Shopify query was rejected or customer fields are inaccessible."
            )
        return result["data"]

    def order(self, external_id):
        fields = """id name updatedAt createdAt cancelledAt note phone tags currencyCode
            displayFinancialStatus displayFulfillmentStatus paymentGatewayNames taxesIncluded
            currentSubtotalPriceSet { shopMoney { amount currencyCode } }
            currentTotalTaxSet { shopMoney { amount currencyCode } }
            currentTotalDiscountsSet { shopMoney { amount currencyCode } }
            totalShippingPriceSet { shopMoney { amount currencyCode } }
            currentTotalPriceSet { shopMoney { amount currencyCode } }
            totalOutstandingSet { shopMoney { amount currencyCode } }
            customer { id displayName phone }
            customAttributes { key value }
            shippingAddress { name phone address1 address2 city province country zip latitude longitude }
            lineItems(first:100) { nodes { id name sku quantity currentQuantity
                originalUnitPriceSet { shopMoney { amount currencyCode } }
                taxLines { title rate priceSet { shopMoney { amount currencyCode } } }
                variant { id sku } } pageInfo { hasNextPage } }"""
        node = self.graphql("query($id:ID!){order(id:$id){" + fields + "}}", {"id": external_id})[
            "order"
        ]
        if not node:
            raise IntegrationError("Shopify order was not found or is inaccessible.")
        if node["lineItems"]["pageInfo"]["hasNextPage"]:
            raise IntegrationError(
                "Order has over 100 lines and needs extended line pagination before import."
            )
        return node

    def recent_ids(self, since):
        cursor = None
        while True:
            data = self.graphql(
                """query($query:String!,$cursor:String){orders(first:50,after:$cursor,query:$query,sortKey:UPDATED_AT){
                nodes{id} pageInfo{hasNextPage endCursor}}}""",
                {"query": f"updated_at:>='{since.isoformat()}'", "cursor": cursor},
            )["orders"]
            yield from (node["id"] for node in data["nodes"])
            if not data["pageInfo"]["hasNextPage"]:
                return
            cursor = data["pageInfo"]["endCursor"]


class RPCTransport(xmlrpc.client.SafeTransport):
    def make_connection(self, host):
        connection = super().make_connection(host)
        connection.timeout = 20
        return connection


class Odoo:
    def __init__(self):
        self.url = secret("ODOO_BASE_URL").rstrip("/")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.query
            or parsed.fragment
        ):
            raise IntegrationError("Odoo requires an HTTPS base URL without embedded credentials.")
        self.version = secret("ODOO_VERSION").split(".")[0]
        if self.version not in {"17", "18", "19"}:
            raise IntegrationError(
                "This adapter supports Odoo 17/18 XML-RPC or Odoo 19 JSON-2. Verify other deployed versions before enabling them."
            )
        self.key = secret("ODOO_API_KEY")
        self.database = secret("ODOO_DATABASE")
        self.uid = None

    def call(self, model, method, **kwargs):
        if os.environ.get("ODOO_TRIAL_READ_ONLY") == "true" and method not in {
            "search_read",
            "read",
            "search",
            "search_count",
            "fields_get",
        }:
            raise IntegrationError("The local Odoo Training connection is read-only.")
        if self.version == "19":
            return request_json(
                f"{self.url}/json/2/{model}/{method}",
                kwargs,
                {
                    "Authorization": f"bearer {self.key}",
                    "X-Odoo-Database": self.database,
                    "User-Agent": "OPSDEMO-Operations",
                },
            )
        try:
            if self.uid is None:
                common = xmlrpc.client.ServerProxy(
                    f"{self.url}/xmlrpc/2/common", transport=RPCTransport()
                )
                self.uid = common.authenticate(self.database, secret("ODOO_LOGIN"), self.key, {})
                if not self.uid:
                    raise IntegrationError("Odoo authentication failed.")
            proxy = xmlrpc.client.ServerProxy(
                f"{self.url}/xmlrpc/2/object", transport=RPCTransport()
            )
            values = kwargs.copy()
            if method == "create":
                args = [values.pop("vals_list")[0]]
            elif method == "write":
                args = [values.pop("ids"), values.pop("vals")]
            elif method in {"action_confirm"}:
                args = [values.pop("ids")]
            else:
                args = []
            return proxy.execute_kw(self.database, self.uid, self.key, model, method, args, values)
        except xmlrpc.client.Fault as exc:
            raise IntegrationError(
                "Odoo rejected the model operation. Check access rights and required fields."
            ) from exc
        except (OSError, xmlrpc.client.ProtocolError) as exc:
            raise TransientError("Odoo connection was unsuccessful.") from exc

    def find(self, key):
        return self.call(
            "sale.order",
            "search_read",
            domain=["|", ["client_order_ref", "=", key], ["origin", "=", key]],
            fields=["id", "name", "currency_id", "amount_total", "state"],
            limit=2,
        )

    def create(self, values):
        try:
            result = self.call("sale.order", "create", vals_list=[values])
            if isinstance(result, list) and len(result) == 1:
                result = result[0]
            if type(result) is not int or result <= 0:
                raise UnknownCreateResult(
                    "Quotation creation returned no valid record ID. Reconcile before any retry."
                )
            return result
        except (TransientError, TimeoutError) as exc:
            raise UnknownCreateResult(
                "Quotation creation result is unknown. Reconcile before any retry."
            ) from exc
