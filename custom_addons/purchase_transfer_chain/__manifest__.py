{
    "name": "Purchase Transfer Chain",
    "version": "17.0.1.0.1",
    "summary": "Show receipts and their follow-up transfers on the purchase order",
    "description": """
With 2/3-step receptions, the standard PO smart button only counts the receipt (step 1).
This module adds a "Transfers" smart button that follows the stock move chain
(move_dest_ids) and lists the receipt together with its follow-up transfers
(e.g. QC Process / Input -> Stock).

The standard picking_ids field is left untouched, so receipt status and
adding products when the PO is revised keep working as usual.
""",
    "category": "Inventory/Purchase",
    "author": "Delfi",
    "license": "LGPL-3",
    "depends": ["purchase_stock"],
    "data": [
        "views/purchase_order_views.xml",
    ],
    "installable": True,
}
