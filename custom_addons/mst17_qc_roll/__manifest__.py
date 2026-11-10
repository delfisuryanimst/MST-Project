{
    "name": "QC Roll (Tahap 2)",
    "version": "17.0.1.3.0",
    "summary": "QC per roll: one Validate creates the Scrap / Simplified MO, the lot name never changes",
    "description": """
QC Tahap 2 - ID Roll (part A)
=============================
Built from the TSD "QC Tahap 2 - ID Roll" (6 Nov 2026), in its own menu. The old Quality Control
flow (Quality Alert > Execute) is not changed.

* Roll data moves from the lot name to fields on the lot (Lot Warna), with a roll history.
* QC fills the result per roll; a QC Koordinator presses Validate. Odoo then writes the data,
  makes the Simplified MO (qty correction / downgrade, same lot name) and the Scrap, all done.
* Rolls that did not pass fully get an automatic, read only QC Roll Alert.

Not in this part: split roll, product change A to A, cancel result, EPC / label reprint,
MST Mobile and Finance valuation rules.
""",
    "category": "Inventory/Quality",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["stock", "mail", "quality_control"],
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
