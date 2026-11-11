{
    "name": "MST Data Pengiriman",
    "version": "17.0.1.0.0",
    "summary": "Delivered quantity per salesperson, team, customer and product (Bruto / Netto), with Excel export",
    "category": "Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "account"],
    "data": [
        "security/sales_delivery_security.xml",
        "security/ir.model.access.csv",
        "data/ir_cron.xml",
        "views/sales_delivery_views.xml",
        "views/stock_picking_views.xml",
        "views/product_category_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mst17_sales_delivery/static/src/scss/sales_delivery.scss",
            "mst17_sales_delivery/static/src/delivery_result/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
