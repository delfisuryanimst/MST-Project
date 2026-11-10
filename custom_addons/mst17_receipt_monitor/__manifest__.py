{
    "name": "MST Monitor Penerimaan",
    "version": "17.0.1.0.0",
    "summary": "Inventory > Monitoring > Monitor Penerimaan: WH/IN received, pending, delayed and late per date",
    "category": "Inventory/Inventory",
    "author": "Impact",
    "license": "LGPL-3",
    "depends": ["stock", "purchase", "web"],
    "data": [
        "security/ir.model.access.csv",
        "views/receipt_monitor_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mst17_receipt_monitor/static/src/receipt_monitor.js",
            "mst17_receipt_monitor/static/src/receipt_monitor.xml",
            "mst17_receipt_monitor/static/src/receipt_monitor.scss",
        ],
    },
    "installable": True,
    "application": False,
}
