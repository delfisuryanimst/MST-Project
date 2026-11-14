{
    "name": "MST Sales Booking",
    "version": "17.0.1.0.0",
    "summary": "Hold stock for a customer while the sale is still being negotiated",
    "description": """
Sales Booking
=============
* A booking (BOOK/ finished goods, RM/ greige) holds free stock of a warehouse for one customer.
  Salespeople confirm it, a Sales Manager approves it; managers book directly.
* "Book Stock" on a quotation makes the booking from the order lines.
* On order confirmation the booked quantity is used: the hold is freed for the delivery and
  the rest stays booked. A booking is closed when everything is used.
* Release frees the rest by hand; a daily job releases bookings past their Valid Until date.
""",
    "category": "Sales/Sales",
    "author": "Delf",
    "license": "LGPL-3",
    "depends": ["sale_stock", "mail"],
    "data": [
        "security/sales_booking_security.xml",
        "security/ir.model.access.csv",
        "data/sales_booking_data.xml",
        "views/sale_booking_views.xml",
        "views/sale_order_views.xml",
    ],
    "installable": True,
}
