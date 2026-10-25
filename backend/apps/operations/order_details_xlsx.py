"""Native XLSX report matching the supplied monthly order-status tables.

All user values are inline strings, never formulas. Only numeric dates are numeric cells.
"""

import re
from collections import defaultdict
from datetime import date, datetime
from io import BytesIO
from xml.sax.saxutils import escape
from zipfile import ZIP_DEFLATED, ZipFile

from django.http import HttpResponse
from django.utils import timezone

HEADERS = [
    "Website Order Number",
    "Odoo Order Number",
    "Customer Name (Full Name)",
    "Customer Phone Number",
    "Creation Date",
    "Status",
    "Order delivery date",
    "Courier",
    "Area",
    "Notes",
]
WIDTHS = [23, 22, 36, 27, 24, 23, 24, 18, 20, 44]
STATUS_COLORS = {
    "Delivered": ("2A914C", "FFFFFF"),
    "Cancelled": ("E83030", "FFFFFF"),
    "Pending": ("3ED9D3", "000000"),
    "Out for Delivery": ("E67A18", "FFFFFF"),
    "Postponed": ("A5479B", "FFFFFF"),
    "Not Delivered": ("8A8A8A", "FFFFFF"),
    "Preparing": ("FFF2D8", "895E12"),
    "Ready": ("E0F1E4", "255C37"),
    "Assigned": ("E4EEF8", "245281"),
}
NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def text(value):
    return escape(
        re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str("" if value is None else value))[:32767]
    )


def cell(address, value, style=0):
    if value is None:
        return f'<c r="{address}" s="{style}"/>'
    if isinstance(value, (date, datetime)):
        if isinstance(value, datetime):
            value = value.replace(tzinfo=None)
        else:
            value = datetime.combine(value, datetime.min.time())
        serial = (value - datetime(1899, 12, 30)).total_seconds() / 86400
        return f'<c r="{address}" s="{style}"><v>{serial}</v></c>'
    return f'<c r="{address}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{text(value)}</t></is></c>'


def styles():
    fonts = '<font><sz val="11"/><name val="Arial"/></font><font><b/><sz val="11"/><name val="Arial"/></font>'
    fills = '<fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFD1D1CD"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFDFDFDC"/></patternFill></fill><fill><patternFill patternType="solid"><fgColor rgb="FFF2F2F0"/></patternFill></fill>'
    xfs = []
    for fill, font, fmt in [
        (0, 0, 0),
        (2, 1, 0),
        (3, 0, 0),
        (4, 0, 0),
        (3, 0, 164),
        (4, 0, 164),
        (3, 0, 165),
        (4, 0, 165),
        (3, 0, 49),
        (4, 0, 49),
    ]:
        xfs.append(
            f'<xf numFmtId="{fmt}" fontId="{font}" fillId="{fill}" borderId="0" xfId="0" applyAlignment="1" applyNumberFormat="1"><alignment horizontal="center" vertical="center" wrapText="1"/></xf>'
        )
    dxfs = "".join(
        f'<dxf><font><b/><color rgb="FF{font}"/></font><fill><patternFill patternType="solid"><fgColor rgb="FF{fill}"/><bgColor rgb="FF{fill}"/></patternFill></fill></dxf>'
        for fill, font in STATUS_COLORS.values()
    )
    return f'<styleSheet xmlns="{NS}"><numFmts count="2"><numFmt numFmtId="164" formatCode="yyyy-mm-dd hh:mm"/><numFmt numFmtId="165" formatCode="yyyy-mm-dd"/></numFmts><fonts count="2">{fonts}</fonts><fills count="5">{fills}</fills><borders count="1"><border/></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="10">{"".join(xfs)}</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles><dxfs count="{len(STATUS_COLORS)}">{dxfs}</dxfs><tableStyles count="0" defaultTableStyle="TableStyleLight1"/></styleSheet>'


def workbook_bytes(groups):
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as z:
        z.writestr(
            "_rels/.rels",
            f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        )
        sheets = []
        relations = []
        overrides = []
        for i, (name, rows) in enumerate(groups.items(), 1):
            n = len(rows) + 1
            sheets.append(f'<sheet name="{text(name)}" sheetId="{i}" r:id="rId{i}"/>')
            relations.append(
                f'<Relationship Id="rId{i}" Type="{REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
            )
            for part, typ in [
                (f"worksheets/sheet{i}.xml", "worksheet"),
                (f"tables/table{i}.xml", "table"),
            ]:
                overrides.append(
                    f'<Override PartName="/xl/{part}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.{typ}+xml"/>'
                )
            header = (
                '<row r="1" ht="30" customHeight="1">'
                + "".join(cell(f"{chr(65 + c)}1", v, 1) for c, v in enumerate(HEADERS))
                + "</row>"
            )
            body = []
            for r, values in enumerate(rows, 2):
                stripe = (r - 2) % 2

                def style(c):
                    return (
                        4 + stripe
                        if c == 4
                        else 6 + stripe
                        if c == 6
                        else 8 + stripe
                        if c == 3
                        else 2 + stripe
                    )

                body.append(
                    f'<row r="{r}" ht="30" customHeight="1">'
                    + "".join(cell(f"{chr(65 + c)}{r}", v, style(c)) for c, v in enumerate(values))
                    + "</row>"
                )
            cf = ""
            if rows:
                cf = (
                    f'<conditionalFormatting sqref="F2:F{n}">'
                    + "".join(
                        f'<cfRule type="cellIs" dxfId="{p}" priority="{p + 1}" operator="equal"><formula>"{text(status)}"</formula></cfRule>'
                        for p, status in enumerate(STATUS_COLORS)
                    )
                    + "</conditionalFormatting>"
                )
            cols = "".join(
                f'<col min="{c}" max="{c}" width="{width}" customWidth="1"/>'
                for c, width in enumerate(WIDTHS, 1)
            )
            validation = (
                f'<dataValidations count="1"><dataValidation type="list" allowBlank="1" showErrorMessage="1" sqref="F2:F{n}"><formula1>"{",".join(STATUS_COLORS)}"</formula1></dataValidation></dataValidations>'
                if rows
                else ""
            )
            z.writestr(
                f"xl/worksheets/sheet{i}.xml",
                f'<worksheet xmlns="{NS}" xmlns:r="{REL}"><dimension ref="A1:J{n}"/><sheetViews><sheetView workbookViewId="0"><pane xSplit="2" topLeftCell="C1" activePane="topRight" state="frozen"/></sheetView></sheetViews><cols>{cols}</cols><sheetData>{header}{"".join(body)}</sheetData>{cf}{validation}<tableParts count="1"><tablePart r:id="rId1"/></tableParts></worksheet>',
            )
            z.writestr(
                f"xl/worksheets/_rels/sheet{i}.xml.rels",
                f'<Relationships xmlns="{PKG}"><Relationship Id="rId1" Type="{REL}/table" Target="../tables/table{i}.xml"/></Relationships>',
            )
            columns = "".join(
                f'<tableColumn id="{c}" name="{text(label)}"/>'
                for c, label in enumerate(HEADERS, 1)
            )
            z.writestr(
                f"xl/tables/table{i}.xml",
                f'<table xmlns="{NS}" id="{i}" name="Orders_{i}" displayName="Orders_{i}" ref="A1:J{n}" totalsRowShown="0"><autoFilter ref="A1:J{n}"/><tableColumns count="10">{columns}</tableColumns><tableStyleInfo name="TableStyleLight1" showFirstColumn="0" showLastColumn="0" showRowStripes="1" showColumnStripes="0"/></table>',
            )
        relations.append(f'<Relationship Id="styles" Type="{REL}/styles" Target="styles.xml"/>')
        z.writestr(
            "xl/workbook.xml",
            f'<workbook xmlns="{NS}" xmlns:r="{REL}"><sheets>{"".join(sheets)}</sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            f'<Relationships xmlns="{PKG}">{"".join(relations)}</Relationships>',
        )
        z.writestr("xl/styles.xml", styles())
        z.writestr(
            "[Content_Types].xml",
            f'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>{"".join(overrides)}</Types>',
        )
    return out.getvalue()


def order_values(order):
    source = getattr(order, "shopify", None)
    created = source.order_created_at if source else order.created_at
    local = timezone.localtime(created) if created else None
    odoo = getattr(order, "odoo", None)
    status = {"SCHEDULED": "Pending", "OUT_FOR_DELIVERY": "Out for Delivery"}.get(
        order.state, order.get_state_display()
    )
    return [
        source.order_number if source else "",
        odoo.external_number if odoo else "",
        order.customer_name,
        order.customer_phone,
        local,
        status,
        order.delivery_date,
        order.courier.get_full_name() or order.courier.username if order.courier else "",
        order.area.name if order.area else order.original_area,
        order.notes,
    ]


def export_response(orders):
    groups = defaultdict(list)
    for order in orders.select_related("shopify", "odoo").order_by("-created_at"):
        values = order_values(order)
        local = values[4]
        month = local.strftime("%b %Y") if local else "Creation date unknown"
        groups[month].append(values)
    if not groups:
        groups["Orders"] = []
    response = HttpResponse(
        workbook_bytes(groups),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = 'attachment; filename="opsdemo-order-details.xlsx"'
    return response
