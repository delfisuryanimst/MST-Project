{
    "name": "MST Permintaan Sampel",
    "version": "17.0.1.0.0",
    "summary": "Sales request fabric samples; the cutting admin processes them into an Internal Transfer or a Quotation",
    "description": """
Permintaan Sampel (FDD "Permintaan Sampel v1", mockup 2 boards)
===============================================================
* Sales (or Admin Sales on behalf of a user) fill customer, products (camera scan on phones), free / paid
  and the price per yard, then press Ajukan. The destination comes from the Sales Team of the requester.
* The cutting admin (Inventory menu, no Sales rights needed) gets an activity, picks the source location
  (proposed: most free stock among the sample source locations) and the cut qty, then presses Proses:
  free -> Internal Transfer per source location, paid -> Quotation with the sales price.
* The request becomes Selesai when its transfers are done or its quotation is confirmed.
* Report per customer / sales / product / month, processing time and conversion to a Sales Order in 90 days.
""",
    "category": "Sales/Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "mail"],
    "data": [
        "security/sample_request_security.xml",
        "security/ir.model.access.csv",
        "data/sample_request_data.xml",
        "views/sample_request_views.xml",
        "views/sample_request_report_views.xml",
        "views/config_views.xml",
        "wizard/sample_request_wizard_views.xml",
        "views/sample_request_menus.xml",
    ],
    "installable": True,
}
