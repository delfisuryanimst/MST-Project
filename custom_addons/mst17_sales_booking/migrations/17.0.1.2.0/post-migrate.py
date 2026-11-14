def migrate(cr, version):
    # Booking Form is greige only now (finished goods use Book Stock), so the FG/ sequence is gone.
    cr.execute("DELETE FROM ir_model_data WHERE module = 'mst17_sales_booking' AND name = 'seq_sale_booking_fg'")
    cr.execute("DELETE FROM ir_sequence WHERE code = 'mst.sale.booking.fg'")
