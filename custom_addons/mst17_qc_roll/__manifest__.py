{
    "name": "QC Roll",
    "version": "17.0.1.4.0",
    "summary": "QC per roll: one Validate creates the Scrap / Simplified MO, the lot name never changes",
    "description": """
QC - ID Roll
====================
Built from the FDD "QC ID Roll" (IT Development Request Form v1, mockup 9 boards), in its own menu.
The old Quality Control flow (Quality Alert > Execute) is not changed: both menus run side by side.

* ID Roll: from the company's start date a new receipt lot is named R26-000123 (EPC v4 when the lot
  has the live field x_epc). Roll data (Lot Warna, No. Pcs, PO, K3L, Nama ASI) are lot fields, with a
  roll history (received, sent to QC, moved, QC results, sent to customer).
* QC fills the result per roll; a QC Koordinator / Group Leader presses Validate. Odoo writes the data,
  makes the Simplified MO (qty correction, split, product change A to A, downgrade; same lot name)
  and the Scrap, all done, and an automatic read only QC Roll Alert.

Not built yet: mass Lot Warna, label reprint flags, Carry / Tolerance valuation, QC difference report,
cancel result, new names for old-format lots.
""",
    "category": "Inventory/Quality",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["purchase_stock", "mail", "quality_control"],
    "data": [
        "security/mst_qc_roll_security.xml",
        "security/ir.model.access.csv",
        "data/mst_qc_roll_sequence.xml",
        "views/mst_qc_inspection_views.xml",
        "views/mst_qc_alert_views.xml",
        "views/mst_roll_history_views.xml",
        "views/stock_lot_views.xml",
        "views/res_company_views.xml",
        "wizard/mst_qc_load_rolls_views.xml",
        "views/mst_qc_roll_menus.xml",
    ],
    "application": True,
    "installable": True,
}
