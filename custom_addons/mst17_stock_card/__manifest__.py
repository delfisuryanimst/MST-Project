{
    "name": "MST Kartu Stok",
    "version": "17.0.1.0.0",
    "summary": "Inventory > Reporting > Kartu Stok: stock card per product with Warehouse, Location and Rak filters",
    "category": "Inventory/Inventory",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "purchase_stock"],
    "data": [
        "security/ir.model.access.csv",
        "views/stock_card_views.xml",
    ],
    "installable": True,
}
