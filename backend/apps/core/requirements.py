"""Exact decimal demand calculations; allocation and locks belong to inventory services."""

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal


def quantity(value: Decimal, name: str) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value < 0:
        raise ValueError(f"{name} must be a finite, nonnegative Decimal")
    return value


@dataclass(frozen=True)
class ComponentDemand:
    order_line_id: str
    material_id: str
    finished_quantity: Decimal
    quantity_per_output: Decimal
    output_yield: Decimal = Decimal("1")

    def required(self) -> Decimal:
        amount = quantity(self.finished_quantity, "Finished quantity")
        per_output = quantity(self.quantity_per_output, "Component quantity")
        output = quantity(self.output_yield, "Output yield")
        if output == 0:
            raise ValueError("Output yield must be positive")
        return amount * per_output / output


def aggregate(demands: list[ComponentDemand]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for demand in demands:
        totals[demand.material_id] += demand.required()
    return dict(totals)


def stock_requirement(required: Decimal, on_hand: Decimal, reserved: Decimal) -> dict[str, Decimal]:
    required = quantity(required, "Required quantity")
    on_hand = quantity(on_hand, "On-hand quantity")
    reserved = quantity(reserved, "Reserved quantity")
    if reserved > on_hand:
        raise ValueError("Reservations exceed usable on-hand stock")
    available = on_hand - reserved
    return {
        "required": required,
        "on_hand": on_hand,
        "reserved": reserved,
        "available": available,
        "shortage": max(Decimal("0"), required - available),
        "remaining": max(Decimal("0"), available - required),
    }
