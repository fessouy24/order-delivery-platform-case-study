"""Shopify fulfillment transport shared by the application and one-time Excel tool."""

from .providers import IntegrationError, TransientError, UnknownCreateResult

SNAPSHOT = """query($id:ID!){order(id:$id){id name cancelledAt displayFinancialStatus displayFulfillmentStatus
 currentTotalPriceSet{shopMoney{amount currencyCode}}
 lineItems(first:100){nodes{id sku currentQuantity} pageInfo{hasNextPage}}
 fulfillmentOrders(first:100){nodes{id status supportedActions{action} assignedLocation{location{id}}
 lineItems(first:100){nodes{id remainingQuantity totalQuantity lineItem{id}} pageInfo{hasNextPage}}} pageInfo{hasNextPage}}}}"""


def permissions(client):
    data = client.graphql(
        "query{shop{myshopifyDomain} currentAppInstallation{accessScopes{handle}}}", {}
    )
    if data["shop"]["myshopifyDomain"].lower() != client.store:
        raise IntegrationError("Shopify store identity does not match configuration.")
    scopes = {row["handle"] for row in data["currentAppInstallation"]["accessScopes"]}
    required = {
        "read_orders",
        "read_merchant_managed_fulfillment_orders",
        "write_merchant_managed_fulfillment_orders",
    }
    missing = required - scopes
    if not scopes & {"read_locations", "read_inventory", "read_markets_home"}:
        missing.add("read_locations")
    if missing:
        raise IntegrationError("Shopify app needs permissions: " + ", ".join(sorted(missing)))
    return sorted(scopes)


def snapshot(client, external_id):
    node = client.graphql(SNAPSHOT, {"id": external_id})["order"]
    if not node:
        raise IntegrationError("Shopify order was not found or is inaccessible.")
    if node.get("cancelledAt"):
        raise IntegrationError("Cancelled Shopify orders cannot be fulfilled.")
    if (
        node["lineItems"]["pageInfo"]["hasNextPage"]
        or node["fulfillmentOrders"]["pageInfo"]["hasNextPage"]
    ):
        raise IntegrationError(
            "Order exceeds the supported 100-line or fulfillment-order limit; review manually."
        )
    lines = []
    for fo in node["fulfillmentOrders"]["nodes"]:
        if fo["lineItems"]["pageInfo"]["hasNextPage"]:
            raise IntegrationError("Fulfillment order exceeds 100 lines; review manually.")
        for line in fo["lineItems"]["nodes"]:
            if line["remainingQuantity"] > 0:
                location = (fo.get("assignedLocation") or {}).get("location") or {}
                if (
                    fo["status"] not in {"OPEN", "IN_PROGRESS"}
                    or "CREATE_FULFILLMENT"
                    not in {action["action"] for action in fo.get("supportedActions", [])}
                    or not location.get("id")
                ):
                    raise IntegrationError(
                        "A remaining fulfillment is held, scheduled or not managed by this app. Review in Shopify."
                    )
                lines.append(
                    {
                        "fo": fo["id"],
                        "id": line["id"],
                        "quantity": line["remainingQuantity"],
                        "total": line["totalQuantity"],
                        "line": line["lineItem"]["id"],
                        "location": location["id"],
                    }
                )
    fulfilled = node["displayFulfillmentStatus"] == "FULFILLED"
    if not lines and not fulfilled:
        raise IntegrationError(
            "No accessible remaining fulfillment items; review permissions or Shopify status."
        )
    if node["displayFinancialStatus"] in {"REFUNDED", "PARTIALLY_REFUNDED", "VOIDED", "EXPIRED"}:
        raise IntegrationError("Refunded or voided orders need review before fulfillment.")
    return {
        "id": node["id"],
        "name": node["name"],
        "financial_status": node["displayFinancialStatus"],
        "fulfilled": fulfilled,
        "signature": sorted([[r["id"], r["currentQuantity"]] for r in node["lineItems"]["nodes"]]),
        "order_lines": [
            [r.get("sku") or "", r["currentQuantity"]] for r in node["lineItems"]["nodes"]
        ],
        "money": node["currentTotalPriceSet"]["shopMoney"],
        "lines": sorted(lines, key=lambda r: r["id"]),
    }


def remaining_plan(approved, current):
    if (
        approved["id"] != current["id"]
        or approved["signature"] != current["signature"]
        or approved.get("money") != current.get("money")
    ):
        raise IntegrationError(
            "Shopify order items changed since approval; review before fulfillment."
        )
    if current["fulfilled"]:
        return []
    old = {line["id"]: line for line in approved["lines"]}
    for line in current["lines"]:
        if line["id"] not in old or line != old[line["id"]]:
            raise IntegrationError(
                "Remaining fulfillment quantities or location changed since approval; review manually."
            )
    return current["lines"]


def groups(lines):
    locations = {}
    for line in lines:
        by_fo = locations.setdefault(line["location"], {})
        by_fo.setdefault(line["fo"], []).append({"id": line["id"], "quantity": line["quantity"]})
    return [
        [
            {"fulfillmentOrderId": fo, "fulfillmentOrderLineItems": items}
            for fo, items in sorted(by_fo.items())
        ]
        for _, by_fo in sorted(locations.items())
    ]


def create(client, group):
    query = """mutation($fulfillment:FulfillmentInput!){fulfillmentCreate(fulfillment:$fulfillment){fulfillment{id status} userErrors{field message}}}"""
    try:
        data = client.graphql(
            query, {"fulfillment": {"notifyCustomer": False, "lineItemsByFulfillmentOrder": group}}
        )["fulfillmentCreate"]
    except TransientError as exc:
        raise UnknownCreateResult(
            "Shopify fulfillment response was interrupted. Reconcile in Shopify before resubmitting."
        ) from exc
    if data.get("userErrors"):
        raise IntegrationError(
            "Shopify rejected fulfillment: "
            + "; ".join(row["message"] for row in data["userErrors"])[:350]
        )
    if not data.get("fulfillment", {}).get("id"):
        raise UnknownCreateResult(
            "Shopify returned no fulfillment ID. Reconcile before resubmitting."
        )
    return data["fulfillment"]["id"]
