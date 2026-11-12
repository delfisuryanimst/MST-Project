{
    "name": "MST Monitor Pengiriman",
    "version": "17.0.1.0.0",
    "summary": "Inventory > Monitoring > Monitor Pengiriman: WH/OUT, WH/RES and WH/MTX of a day, delays and stock issues",
    "category": "Inventory/Inventory",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["mst17_receipt_monitor", "sales_team"],
    "data": [
        "security/ir.model.access.csv",
        "views/stock_config_views.xml",
        "views/delivery_monitor_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mst17_delivery_monitor/static/src/delivery_monitor.js",
            "mst17_delivery_monitor/static/src/delivery_monitor.xml",
            "mst17_delivery_monitor/static/src/delivery_monitor.scss",
        ],
    },
    "installable": True,
}
