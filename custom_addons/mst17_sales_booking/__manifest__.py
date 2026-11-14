{
    "name": "MST Sales Booking",
    "version": "17.0.1.2.0",
    "summary": "Hold stock for a customer while the sale is still being negotiated",
    "description": """
Sales Booking
=============
Two kinds of booking, like the live system:

* Booking Stock (BOOK/): "Book Stock" on a quotation holds the free stock of each stockable line right
  away, no approval. Status Booked / Released / Done. Release (all or part) frees it; on order
  confirmation the line takes what it needs and the rest is freed. Smart button "Booking" on the order,
  menu Inventory > Operations > Stock Reservations.
* Booking Form (RM/ greige): a document with approval
  (Draft > To Approve > Booked > Closed), linked to an order through "Booking Reference".
  Menus Sales > Orders > Booking Form and Booking Form Line.

A daily job releases bookings past their validity date.
""",
    "category": "Sales/Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "mail"],
    "data": [
        "security/sales_booking_security.xml",
        "security/ir.model.access.csv",
        "data/sales_booking_data.xml",
        "wizard/reservation_release_views.xml",
        "views/stock_reservation_views.xml",
        "views/sale_booking_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
}
