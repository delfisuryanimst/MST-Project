{
    "name": "MST Sales Order Journey",
    "version": "17.0.1.0.0",
    "summary": "Show where each sales order line is: production, QC, delivery, invoice",
    "category": "Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_management", "sale_stock", "purchase_stock", "account"],
    "data": [
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/sale_order_line_journey_views.xml",
        "views/sale_order_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mst17_sales_journey/static/src/scss/journey.scss",
        ],
    },
    "installable": True,
    "application": True,
}
