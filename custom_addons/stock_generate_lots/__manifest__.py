{
    "name": "Stock Generate Lots",
    "version": "17.0.1.0.0",
    "summary": "Generate lots by quantity per lot on receipts",
    "description": """
Adds a "Generate Lots" button next to "Import Serials/Lots" in the move detail dialog.
The wizard asks for the first lot number, the quantity per lot and the quantity received,
then creates the lines (e.g. 1050 received / 100 per lot = 10 lots of 100 + 1 lot of 50).
""",
    "category": "Inventory/Inventory",
    "author": "Delfi",
    "license": "LGPL-3",
    "depends": ["stock"],
    "data": [
        "views/stock_move_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "stock_generate_lots/static/src/widgets/generate_lots.js",
            "stock_generate_lots/static/src/widgets/generate_lots.xml",
        ],
    },
    "installable": True,
}
