from decimal import Decimal as D

import pytest
from rest_framework.exceptions import ValidationError

from apps.operations import courier_cash, orders
from apps.operations import finance_completion as fc
from apps.operations.models import (
    Alert,
    ExternalMapping,
    FinanceCompletion,
    IntegrationJob,
)
from tests.test_courier_settlement_and_fulfillment import delivered, receipt

pytestmark = pytest.mark.django_db


def close_website(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    finance = user_factory("Finance")
    courier_cash.settle(courier.pk, receipt(order), finance)
    return order, finance, FinanceCompletion.objects.get(order=order)


def test_cash_handover_completes_once_and_notifies_management(setup_order, user_factory):
    order, actor, row = close_website(setup_order, user_factory)
    assert row.message == "الأوردر دون يا إكسلانس 😂😂"
    assert fc.finish(order.pk, actor).pk == row.pk
    assert (
        Alert.objects.filter(key=f"finance-completed:{order.pk}", target_role="Management").count()
        == 1
    )
    assert row.odoo_status == "PENDING"


def test_completion_rejects_unsettled_cod_and_unauthorized_users(setup_order, user_factory, api):
    order, _ = delivered(setup_order, user_factory)
    with pytest.raises(ValidationError, match="cash handover"):
        fc.finish(order.pk, user_factory("Finance"))
    for role in ["Courier", "Warehouse", "Sales", "Production", "Management"]:
        api.force_authenticate(user_factory(role, username="blocked-" + role))
        assert (
            api.post(
                f"/api/v1/departments/finance/orders/{order.pk}/complete/",
                {"confirm": True},
                format="json",
            ).status_code
            == 403
        )


def test_market_receipt_and_less_than_half_sent_warning(setup_order, user_factory):
    owner, loc, _, _, make = setup_order
    from tests.scenarios import stocked_product

    order = make([(stocked_product(loc, stock="10"), 10)])
    order = orders.market_quantities(
        order.pk,
        order.version,
        owner,
        [{"line": order.lines.get().pk, "quantity": D(4)}],
        "Only four available",
    )
    order = orders.ready(order.pk, order.version, owner)
    order = orders.assign(order.pk, order.version, owner, user_factory("Courier"))
    order = orders.dispatch(order.pk, order.version, owner)
    order = orders.deliver(order.pk, order.version, owner, D(0))
    finance = user_factory("Finance")
    with pytest.raises(ValidationError, match="received"):
        fc.finish(order.pk, finance)
    order = orders.market_quantities(
        order.pk,
        order.version,
        finance,
        [{"line": order.lines.get().pk, "quantity": D(3)}],
        "One refused",
        received=True,
    )
    row = fc.finish(order.pk, finance)
    assert row.kind == "MARKET" and row.warning == fc.WARNING
    assert row.snapshot[0]["received"] == "3.000000"
    with pytest.raises(ValidationError, match="already completed"):
        orders.market_quantities(
            order.pk,
            order.version,
            finance,
            [{"line": order.lines.get().pk, "quantity": D(4)}],
            "Correction",
            received=True,
        )


class FakeOdoo:
    database = "Training"

    def __init__(self, order):
        self.order = order
        self.state = "draft"
        self.invoice = None
        self.values = None
        self.creates = 0
        self.confirms = 0
        self.timeout = False

    def find(self, key):
        return [{"id": 17}]

    def call(self, model, method, **kw):
        if model == "sale.order":
            if method == "action_confirm":
                self.confirms += 1
                self.state = "sale"
                return True
            return [
                {
                    "id": 17,
                    "name": "S-TEST",
                    "state": self.state,
                    "company_id": [1, "Company"],
                    "partner_invoice_id": [4, "Web cash"],
                    "currency_id": [1, "EGP"],
                    "payment_term_id": False,
                    "fiscal_position_id": False,
                    "amount_total": float(self.order.total),
                }
            ]
        if model == "sale.order.line":
            return [
                {
                    "id": 22,
                    "product_id": [8, "Item"],
                    "product_uom_qty": 2,
                    "price_unit": 10,
                    "discount": 0,
                    "tax_id": [],
                    "product_uom": [1, "Units"],
                    "is_downpayment": False,
                }
            ]
        if model == "account.move":
            if method == "create":
                self.creates += 1
                self.values = kw["vals_list"][0]
                self.invoice = {
                    "id": 99,
                    "name": "/",
                    "state": "draft",
                    "move_type": "out_invoice",
                    "company_id": [1, "Company"],
                    "partner_id": [4, "Web cash"],
                    "currency_id": [1, "EGP"],
                }
                if self.timeout:
                    raise TimeoutError("network secret")
                return 99
            domain = kw["domain"]
            return [self.invoice] if domain[0][0] == "ref" and self.invoice else []
        if model == "account.move.line":
            v = self.values["invoice_line_ids"][0][2]
            return [
                {
                    **v,
                    "product_id": [v["product_id"], "Item"],
                    "tax_ids": v["tax_ids"][0][2],
                    "sale_line_ids": v["sale_line_ids"][0][2],
                }
            ]
        raise AssertionError((model, method))


def prepare_remote(monkeypatch, order):
    job = IntegrationJob.objects.get(order=order)
    job.status = "SYNCED"
    job.external_id = "17"
    job.save()
    ExternalMapping.objects.create(
        provider="ODOO", entity_type="item", entity_id=order.lines.get().item_id, external_id="8"
    )
    remote = FakeOdoo(order)
    monkeypatch.setattr(fc, "Odoo", lambda: remote)
    monkeypatch.setenv("TRIAL_MODE", "false")
    monkeypatch.setenv("ODOO_COMPLETION_ENABLED", "true")
    return remote


def test_confirm_and_draft_invoice_are_idempotent_never_posted(
    setup_order, user_factory, monkeypatch
):
    order, _, row = close_website(setup_order, user_factory)
    remote = prepare_remote(monkeypatch, order)
    fc.process(row.pk)
    fc.process(row.pk)
    row.refresh_from_db()
    assert row.odoo_status == "SUCCEEDED" and row.invoice_id == 99
    assert remote.confirms == 1 and remote.creates == 1 and remote.invoice["state"] == "draft"


def test_unknown_invoice_is_reconciled_without_duplicate(setup_order, user_factory, monkeypatch):
    order, _, row = close_website(setup_order, user_factory)
    remote = prepare_remote(monkeypatch, order)
    remote.timeout = True
    fc.process(row.pk)
    row.refresh_from_db()
    assert row.odoo_status == "UNKNOWN"
    fc.process(row.pk)
    row.refresh_from_db()
    assert row.odoo_status == "SUCCEEDED" and remote.creates == 1


def test_trial_mode_never_calls_odoo(setup_order, user_factory, monkeypatch):
    order, _, row = close_website(setup_order, user_factory)
    monkeypatch.setenv("TRIAL_MODE", "true")
    monkeypatch.setenv("ODOO_COMPLETION_ENABLED", "true")
    monkeypatch.setattr(
        fc, "Odoo", lambda: (_ for _ in ()).throw(AssertionError("must not connect"))
    )
    fc.process(row.pk)
    row.refresh_from_db()
    assert row.odoo_status == "PENDING"


def test_draft_uses_accepted_quantity_and_rejects_changed_price(
    setup_order, user_factory, monkeypatch
):
    order, _, row = close_website(setup_order, user_factory)
    remote = prepare_remote(monkeypatch, order)
    row.snapshot[0]["received"] = "1"
    row.save()
    fc.process(row.pk)
    row.refresh_from_db()
    assert row.odoo_status == "SUCCEEDED"
    assert remote.values["invoice_line_ids"][0][2]["quantity"] == 1


def test_completion_messages_include_supply_chain(setup_order, user_factory):
    from apps.operations.views import visible_alerts

    order, _, row = close_website(setup_order, user_factory)
    assert visible_alerts(user_factory("Management")).filter(kind="FINANCE_COMPLETION").exists()
    assert visible_alerts(user_factory("Admin")).filter(kind="FINANCE_COMPLETION").exists()
    assert visible_alerts(user_factory("Supply Chain")).filter(kind="FINANCE_COMPLETION").exists()
    assert not visible_alerts(user_factory("Warehouse")).filter(kind="FINANCE_COMPLETION").exists()


@pytest.mark.django_db
def test_empty_completion_has_explicit_json_null(setup_order, user_factory, api):
    order, _ = delivered(setup_order, user_factory)
    api.force_authenticate(user_factory("Finance"))
    response = api.get(f"/api/v1/departments/finance/orders/{order.pk}/complete/")
    assert response.status_code == 200 and response.data == {"completion": None}


def test_prepaid_notifies_on_delivery_once_before_finance(setup_order, user_factory):
    order, _ = delivered(setup_order, user_factory, online=True)
    assert not FinanceCompletion.objects.filter(order=order).exists()
    notice = Alert.objects.get(key=f"finance-completed:{order.pk}")
    assert fc.MESSAGES["WEBSITE"] in notice.message
    fc.finish(order.pk, user_factory("Finance"))
    assert Alert.objects.filter(key=notice.key).count() == 1


def test_cod_does_not_notify_until_cash_handover(setup_order, user_factory):
    order, courier = delivered(setup_order, user_factory)
    assert not Alert.objects.filter(key=f"finance-completed:{order.pk}").exists()
    courier_cash.settle(courier.pk, receipt(order), user_factory("Finance"))
    assert Alert.objects.filter(key=f"finance-completed:{order.pk}").count() == 1


@pytest.mark.parametrize("accepted", [2, 1, 0])
def test_market_notice_waits_for_finance_full_or_partial_receipt(
    setup_order, user_factory, accepted
):
    order, _ = delivered(setup_order, user_factory, market=True)
    assert not Alert.objects.filter(key=f"finance-completed:{order.pk}").exists()
    finance = user_factory("Finance")
    order = orders.market_quantities(
        order.pk,
        order.version,
        finance,
        [{"line": order.lines.get().pk, "quantity": D(accepted)}],
        "Confirmed actual receipt",
        received=True,
    )
    assert not Alert.objects.filter(key=f"finance-completed:{order.pk}").exists()
    row = fc.finish(order.pk, finance)
    assert fc.MESSAGES["MARKET"] in Alert.objects.get(key=f"finance-completed:{order.pk}").message
    assert D(row.snapshot[0]["received"]) == D(accepted)
