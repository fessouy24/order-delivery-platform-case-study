from decimal import Decimal as D

import pytest

from apps.core.requirements import ComponentDemand, aggregate, stock_requirement


def test_shared_material_shortage_is_one():
    demand = aggregate(
        [
            ComponentDemand("order-a-line", "material-x", D("1"), D("2")),
            ComponentDemand("order-b-line", "material-x", D("1"), D("3")),
        ]
    )
    result = stock_requirement(demand["material-x"], D("4"), D("0"))
    assert demand["material-x"] == D("5")
    assert result["shortage"] == D("1")


def test_existing_reservations_reduce_available_stock():
    result = stock_requirement(D("40"), D("100"), D("70"))
    assert result["available"] == D("30")
    assert result["shortage"] == D("10")


def test_recipe_yield_and_fractional_material():
    demand = ComponentDemand("line", "material-y", D("5"), D("0.5"), D("1"))
    assert demand.required() == D("2.5")
    assert ComponentDemand("line", "x", D("10"), D("4"), D("2")).required() == D("20")


@pytest.mark.parametrize("value", [D("NaN"), D("Infinity"), D("-1"), 1.1])
def test_invalid_quantities_are_rejected(value):
    with pytest.raises(ValueError):
        stock_requirement(value, D("10"), D("0"))


def test_inconsistent_stock_is_rejected():
    with pytest.raises(ValueError):
        stock_requirement(D("1"), D("2"), D("3"))
