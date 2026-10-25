"""Human references for courier screens; internal tracking IDs stay internal."""

import re


def label(order, arabic=False):
    if order.source == "SHOPIFY":
        reference = (
            getattr(getattr(order, "shopify", None), "order_number", "").strip().lstrip("#").strip()
        )
        return (
            ("ويب سايت" if arabic else "Website")
            + ": "
            + (reference or ("رقم الطلب غير مسجل" if arabic else "Number not recorded"))
        )
    name = order.customer_name.strip()
    branch = order.address.branch_code.strip()
    if any(token in name.casefold() for token in ["demo retailer", "متجر تجريبي"]):
        match = re.search(r"\bDS[\s_-]*0*(\d+)\b", branch, re.I)
        if match:
            branch = "DS" + str(int(match.group(1)))
        name = "متجر تجريبي" if arabic else "Demo Retailer"
    if not name:
        name = "طلب خاص" if arabic else "Special order"
    return f"{name}: {branch}" if branch else name
