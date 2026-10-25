import copy
import uuid
from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import (
    courier_cash,
    orders,
)
from apps.operations import (
    fulfillment_transport as transport,
)
from apps.operations import (
    shopify_fulfillment as fulfillment,
)
from apps.operations.models import (
    CourierCashEntry,
    CourierSettlement,
    Order,
    ShopifyFulfillmentJob,
    ShopifyReference,
)
from apps.operations.providers import IntegrationError, TransientError, UnknownCreateResult
from tests.scenarios import stocked_product

pytestmark = pytest.mark.django_db


def delivered(setup_order, user_factory, online=False, market=False, courier=None, sku="CASH"):
    owner, loc, _, _, make = setup_order
    item = stocked_product(loc, sku=sku)
    order = make(
        [(item, 2)],
        payment_method="ONLINE" if online else "COD",
        payment_status="PAID" if online else "UNPAID",
        expected_collection=D(0) if online else D(20),
    )
    if not market:
        order.source = Order.Source.SHOPIFY
        order.save(update_fields=["source"])
        ShopifyReference.objects.create(
            order=order,
            store="opsdemo.myshopify.com",
            external_id="gid://shopify/Order/" + str(uuid.uuid4().int),
            order_number="#" + str(uuid.uuid4().int)[:8],
        )
    courier = courier or user_factory("Courier")
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, courier)
    order = orders.dispatch(order.pk, order.version, owner)
    return orders.deliver(
        order.pk, order.version, courier, D(0) if online or market else D(20)
    ), courier


def receipt(order, key="cash-receipt", amount="20"):
    return {
        "command_key": key,
        "currency": order.currency,
        "entry_ids": [order.cash_entry.pk],
        "amount": D(amount),
        "reference": "Office receipt 21",
    }


def test_cod_requires_finance_and_zeroes_only_after_receipt(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    assert courier_cash.balance(courier.pk) == {"EGP": "20.00"}
    assert not ShopifyFulfillmentJob.objects.filter(order=order).exists()
    finance = user_factory("Finance")
    data = receipt(order)
    handover = courier_cash.settle(courier.pk, data, finance)
    assert courier_cash.balance(courier.pk) == {}
    assert ShopifyFulfillmentJob.objects.get(order=order).gate == "COD_SETTLED"
    assert courier_cash.settle(courier.pk, data, finance).pk == handover.pk
    assert CourierSettlement.objects.count() == 1
    with pytest.raises(ValidationError):
        courier_cash.settle(courier.pk, {**data, "amount": D(19)}, finance)


def test_short_handover_never_clears_debt(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    with pytest.raises(ValidationError):
        courier_cash.settle(courier.pk, receipt(order, amount="19"), user_factory("Finance"))
    assert courier_cash.balance(courier.pk) == {"EGP": "20.00"}
    assert not CourierSettlement.objects.exists() and not ShopifyFulfillmentJob.objects.exists()


def test_new_delivery_requires_refreshed_complete_balance(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    data = receipt(order)
    delivered(setup_order, user_factory, courier=courier, sku="NEXT")
    with pytest.raises(ValidationError):
        courier_cash.settle(courier.pk, data, user_factory("Finance"))
    assert courier_cash.balance(courier.pk) == {"EGP": "40.00"}


def test_paid_online_queues_on_delivery_and_market_does_not(setup_order, user_factory):
    web, courier = delivered(setup_order, user_factory, online=True)
    assert ShopifyFulfillmentJob.objects.get(order=web).gate == "ONLINE_PAID"
    assert not CourierCashEntry.objects.filter(order=web).exists()
    market, _ = delivered(setup_order, user_factory, market=True, courier=courier, sku="MARKET")
    assert market.collected == 0 and not CourierCashEntry.objects.filter(order=market).exists()
    assert not ShopifyFulfillmentJob.objects.filter(order=market).exists()


def test_cash_permissions_and_cross_courier_isolation(setup_order, user_factory, api):
    order, courier = delivered(setup_order, user_factory)
    other = user_factory("Courier", "other-courier")
    api.force_authenticate(other)
    assert api.get("/api/v1/courier/cash/").json()["balance"] == {}
    data = {**receipt(order), "amount": "20", "entry_ids": [str(order.cash_entry.pk)]}
    assert (
        api.post(
            f"/api/v1/departments/finance/couriers/{courier.pk}/complete/", data, format="json"
        ).status_code
        == 403
    )
    api.force_authenticate(user_factory("Finance"))
    assert (
        api.post(
            f"/api/v1/departments/finance/couriers/{other.pk}/complete/", data, format="json"
        ).status_code
        == 400
    )
    assert (
        api.post(
            f"/api/v1/departments/finance/couriers/{courier.pk}/complete/", data, format="json"
        ).status_code
        == 200
    )


def test_balances_do_not_mix_currencies_or_other_couriers(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    other, _ = delivered(setup_order, user_factory, courier=courier, sku="USD")
    entry = other.cash_entry
    entry.currency = "USD"
    entry.save()
    assert courier_cash.balance(courier.pk) == {"EGP": "20.00", "USD": "20.00"}


def snapshot(order, fulfilled=False):
    return {
        "id": order.shopify.external_id,
        "name": order.shopify.order_number,
        "financial_status": "PAID",
        "fulfilled": fulfilled,
        "signature": [["line", 2]],
        "order_lines": [[order.lines.get().sku, 2]],
        "money": {"amount": "20.00", "currencyCode": "EGP"},
        "lines": []
        if fulfilled
        else [
            {
                "fo": "fo",
                "id": "foline",
                "quantity": 2,
                "total": 2,
                "line": "line",
                "location": "loc",
            }
        ],
    }


def mock_remote(monkeypatch, order, failed=None):
    class Client:
        store = "opsdemo.myshopify.com"

    monkeypatch.setenv("TRIAL_MODE", "false")
    monkeypatch.setenv("SHOPIFY_FULFILLMENT_ENABLED", "true")
    monkeypatch.setattr(fulfillment, "Shopify", Client)
    monkeypatch.setattr(transport, "permissions", lambda client: [])
    state = {"done": False, "calls": 0}
    monkeypatch.setattr(transport, "snapshot", lambda client, id: snapshot(order, state["done"]))

    def create(client, group):
        state["calls"] += 1
        if failed:
            raise failed
        state["done"] = True
        return "gid://shopify/Fulfillment/123"

    monkeypatch.setattr(transport, "create", create)
    return state


def test_worker_fulfills_once_after_prepaid_delivery(setup_order, user_factory, monkeypatch):
    order, _ = delivered(setup_order, user_factory, online=True)
    job = order.fulfillment_job
    state = mock_remote(monkeypatch, order)
    fulfillment.process(job.pk)
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == "SUCCEEDED" and state["calls"] == 1


def test_unknown_response_never_blindly_duplicates(setup_order, user_factory, monkeypatch):
    order, _ = delivered(setup_order, user_factory, online=True)
    job = order.fulfillment_job
    state = mock_remote(monkeypatch, order, UnknownCreateResult("Interrupted"))
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == "UNKNOWN" and job.pending_group
    fulfillment.process(job.pk)
    assert state["calls"] == 1
    state["done"] = True
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == "SUCCEEDED" and state["calls"] == 1


def test_rejected_mutation_can_retry_after_fix_without_unknown_lock(
    setup_order, user_factory, monkeypatch
):
    order, _ = delivered(setup_order, user_factory, online=True)
    job = order.fulfillment_job
    mock_remote(monkeypatch, order, IntegrationError("Permission denied"))
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == "ERROR" and job.pending_group == []
    state = mock_remote(monkeypatch, order)
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == "SUCCEEDED" and state["calls"] == 1


def test_trial_and_disabled_switch_never_call_provider(setup_order, user_factory, monkeypatch):
    order, _ = delivered(setup_order, user_factory, online=True)

    def forbidden():
        raise AssertionError("No remote call in trial")

    monkeypatch.setattr(fulfillment, "Shopify", forbidden)
    monkeypatch.setenv("TRIAL_MODE", "true")
    monkeypatch.setenv("SHOPIFY_FULFILLMENT_ENABLED", "true")
    fulfillment.process(order.fulfillment_job.pk)


def test_payment_or_product_change_blocks_worker(setup_order, user_factory, monkeypatch):
    order, _ = delivered(setup_order, user_factory, online=True)
    state = mock_remote(monkeypatch, order)
    wrong = snapshot(order)
    wrong["order_lines"] = [["OTHER-SKU", 2]]
    monkeypatch.setattr(transport, "snapshot", lambda client, id: wrong)
    fulfillment.process(order.fulfillment_job.pk)
    order.fulfillment_job.refresh_from_db()
    assert state["calls"] == 0 and order.fulfillment_job.status == "ERROR"


def test_finance_receipt_survives_shopify_failure(setup_order, user_factory, monkeypatch):
    order, courier = delivered(setup_order, user_factory)
    courier_cash.settle(courier.pk, receipt(order), user_factory("Finance"))
    mock_remote(monkeypatch, order, IntegrationError("Unavailable scope"))
    fulfillment.process(order.fulfillment_job.pk)
    assert courier_cash.balance(courier.pk) == {} and CourierSettlement.objects.count() == 1
    assert ShopifyFulfillmentJob.objects.get(order=order).status == "ERROR"


def test_transport_is_explicitly_silent_and_location_grouped():
    class Client:
        def graphql(self, query, variables):
            assert variables["fulfillment"]["notifyCustomer"] is False
            assert variables["fulfillment"]["lineItemsByFulfillmentOrder"][0][
                "fulfillmentOrderLineItems"
            ] == [{"id": "one", "quantity": 2}]
            return {"fulfillmentCreate": {"fulfillment": {"id": "fulfillment"}, "userErrors": []}}

    lines = [
        {"id": "one", "quantity": 2, "fo": "fo1", "location": "a"},
        {"id": "two", "quantity": 1, "fo": "fo2", "location": "b"},
    ]
    groups = transport.groups(lines)
    assert len(groups) == 2
    assert transport.create(Client(), groups[0]) == "fulfillment"


def test_plan_rejects_new_lines_or_changed_remaining_quantities():
    original = {
        "id": "order",
        "signature": [["line", 2]],
        "money": {},
        "lines": [{"id": "one", "quantity": 2}],
    }
    current = copy.deepcopy(original)
    current["fulfilled"] = False
    current["lines"][0]["quantity"] = 1
    with pytest.raises(IntegrationError):
        transport.remaining_plan(original, current)
    current["lines"] = [{"id": "new", "quantity": 2}]
    with pytest.raises(IntegrationError):
        transport.remaining_plan(original, current)


def test_transport_network_interruption_is_unknown():
    class Client:
        def graphql(self, *args):
            raise TransientError("Timeout")

    with pytest.raises(UnknownCreateResult):
        transport.create(Client(), [])


def test_missing_scopes_stops_before_mutations():
    class Client:
        store = "opsdemo.myshopify.com"

        def graphql(self, *args):
            return {
                "shop": {"myshopifyDomain": self.store},
                "currentAppInstallation": {
                    "accessScopes": [{"handle": "read_orders"}, {"handle": "write_fulfillments"}]
                },
            }

    with pytest.raises(IntegrationError, match="write_merchant_managed_fulfillment_orders"):
        transport.permissions(Client())


@pytest.mark.parametrize("during_submit", [False, True])
def test_unexpected_failure_is_reviewable_and_never_replayed(
    setup_order, user_factory, monkeypatch, during_submit
):
    order, _ = delivered(setup_order, user_factory, online=True)
    job = order.fulfillment_job
    state = mock_remote(monkeypatch, order, RuntimeError("private provider response"))
    if not during_submit:
        monkeypatch.setattr(
            transport, "snapshot", lambda *args: (_ for _ in ()).throw(KeyError("private response"))
        )
    fulfillment.process(job.pk)
    job.refresh_from_db()
    assert job.status == ("UNKNOWN" if during_submit else "ERROR")
    assert job.claimed_at is None and job.next_attempt_at is None
    assert "private" not in job.error
    if during_submit:
        assert job.pending_group
        fulfillment.process(job.pk)
        assert state["calls"] == 1


@pytest.mark.parametrize(
    "location_scope", [None, "read_locations", "read_inventory", "read_markets_home"]
)
def test_location_permission_checked_before_snapshot(location_scope):
    class Client:
        store = "opsdemo.myshopify.com"

        def graphql(self, *args):
            scopes = [
                "read_orders",
                "read_merchant_managed_fulfillment_orders",
                "write_merchant_managed_fulfillment_orders",
            ]
            if location_scope:
                scopes.append(location_scope)
            return {
                "shop": {"myshopifyDomain": self.store},
                "currentAppInstallation": {"accessScopes": [{"handle": s} for s in scopes]},
            }

    if location_scope:
        assert location_scope in transport.permissions(Client())
    else:
        with pytest.raises(IntegrationError, match="read_locations"):
            transport.permissions(Client())


def test_finance_handover_generates_reference_without_paper_receipt(setup_order, user_factory, api):
    order, courier = delivered(setup_order, user_factory)
    api.force_authenticate(user_factory("Finance"))
    data = {
        **receipt(order, key="automatic-reference"),
        "amount": "20",
        "entry_ids": [str(order.cash_entry.pk)],
    }
    data.pop("reference")
    url = f"/api/v1/departments/finance/couriers/{courier.pk}/complete/"
    response = api.post(url, data, format="json")
    assert response.status_code == 200, response.data
    assert response.data["reference"].startswith("HANDOVER-")
    assert response.data["balance"] == {}
    repeated = api.post(url, data, format="json")
    assert repeated.status_code == 200
    assert repeated.data["reference"] == response.data["reference"]
    assert repeated.data["receipt"] == response.data["receipt"]
    assert CourierSettlement.objects.count() == 1
    assert order.events.filter(action="finance.order_completed").count() == 1
