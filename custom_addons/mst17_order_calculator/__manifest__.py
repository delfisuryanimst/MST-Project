{
    "name": "MST Order Kalkulator",
    "version": "17.0.1.0.0",
    "summary": "Estimate when a product can be shipped, per product and per quotation line",
    "category": "Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "purchase_stock"],
    "data": [
        "security/ir.model.access.csv",
        "security/order_calculator_security.xml",
        "data/ir_config_parameter.xml",
        "data/ir_cron.xml",
        "views/order_calculator_templates.xml",
        "views/order_calculator_views.xml",
        "views/sale_order_views.xml",
        "views/stock_config_views.xml",
        "wizard/order_calculator_check_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "mst17_order_calculator/static/src/scss/order_calculator.scss",
            "mst17_order_calculator/static/src/order_calc_result/*",
        ],
    },
    "installable": True,
    "application": False,
}
