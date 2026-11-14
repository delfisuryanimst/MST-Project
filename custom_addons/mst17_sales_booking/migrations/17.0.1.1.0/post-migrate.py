def migrate(cr, version):
    # The Released status was dropped (a release now ends as Closed, like the live system), and the
    # BOOK/ prefix now belongs to stock reservations, so finished goods booking forms use FG/.
    cr.execute("UPDATE mst_sale_booking SET state = 'closed' WHERE state = 'released'")
    cr.execute("UPDATE ir_sequence SET prefix = %s, name = %s WHERE code = 'mst.sale.booking.fg'",
               ("FG/%(year)s/", "Booking Form (Finished Goods)"))
    cr.execute("SELECT 1 FROM information_schema.columns "
               "WHERE table_name = 'mst_sale_booking_line' AND column_name = 'utilized_qty'")
    if cr.fetchone():
        cr.execute("UPDATE mst_sale_booking_line SET used_qty = utilized_qty WHERE used_qty IS NULL")
