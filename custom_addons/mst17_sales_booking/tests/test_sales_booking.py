from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSalesBooking(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.wh = env.ref("stock.warehouse0")
        cls.partner = env["res.partner"].create({"name": "Booking Customer"})
        cls.other = env["res.partner"].create({"name": "Other Customer"})
        cls.product = env["product.product"].create({"name": "Booking Fabric", "type": "product"})
        env["stock.quant"]._update_available_quantity(cls.product, cls.wh.lot_stock_id, 100)
        cls.sales = env["res.users"].with_context(no_reset_password=True).create({
            "login": "booking_sales", "name": "Booking Sales",
            "groups_id": [Command.set([env.ref("base.group_user").id,
                                       env.ref("sales_team.group_sale_salesman").id])]})

    def _order(self, qty, partner=None, booking=None):
        return self.env["sale.order"].create({
            "partner_id": (partner or self.partner).id,
            "booking_id": booking and booking.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
        })

    def _free(self):
        return self.product.with_context(location=self.wh.lot_stock_id.id).free_qty

    # ------------------------------------------------------------ Booking Stock (stock reservation)
    def test_book_stock_holds_stock_without_approval(self):
        order = self._order(30)
        order.user_id = self.sales
        order.with_user(self.sales).action_book_stock()
        reservation = order.reservation_ids
        self.assertTrue(reservation.name.startswith("BOOK/"))
        self.assertEqual(reservation.booking_status, "booked")
        self.assertEqual(reservation.move_id.state, "assigned")
        self.assertEqual((order.booking_count, order.can_book_stock), (1, False))
        self.assertEqual(self._free(), 70)
        # Another customer cannot take the booked stock.
        other = self._order(80, partner=self.other)
        other.action_confirm()
        self.assertEqual(other.picking_ids.move_ids.quantity, 70)

    def test_not_enough_stock(self):
        with self.assertRaises(UserError):
            self._order(150).action_book_stock()

    def test_partial_release_then_full(self):
        order = self._order(30)
        order.action_book_stock()
        reservation = order.reservation_ids
        wizard = self.env["mst.stock.reservation.release"].create({"reservation_id": reservation.id, "qty": 10})
        wizard.action_release()
        self.assertEqual((reservation.booking_status, reservation.remaining_qty), ("booked", 20))
        self.assertEqual(reservation.move_id.quantity, 20)
        self.assertEqual(self._free(), 80)
        with self.assertRaises(UserError):
            reservation._release(25)
        reservation._release(20)
        self.assertEqual((reservation.booking_status, reservation.released_qty), ("released", 30))
        self.assertEqual(self._free(), 100)
        # The released reservation stays as history and the quotation can book again.
        self.assertEqual((order.booking_count, order.can_book_stock), (1, True))
        order.action_book_stock()
        self.assertEqual(order.booking_count, 2)
        self.assertEqual(self._free(), 70)

    def test_confirm_uses_reservation(self):
        order = self._order(30)
        order.action_book_stock()
        order.order_line.product_uom_qty = 20
        order.action_confirm()
        reservation = order.reservation_ids
        self.assertEqual(reservation.booking_status, "done")
        self.assertEqual((reservation.used_qty, reservation.released_qty), (20, 10))
        self.assertEqual(reservation.move_id.state, "cancel")
        self.assertEqual(order.picking_ids.move_ids.quantity, 20)
        self.assertEqual(self._free(), 80)

    def test_cancel_order_and_expiry_release(self):
        order = self._order(30)
        order.action_book_stock()
        order._action_cancel()
        self.assertEqual(order.reservation_ids.booking_status, "released")
        self.assertEqual(self._free(), 100)

        expired = self._order(20)
        expired.action_book_stock()
        expired.reservation_ids.date_validity = fields.Date.today() - timedelta(days=1)
        self.env["mst.stock.reservation"]._cron_release_expired()
        self.assertEqual(expired.reservation_ids.booking_status, "released")
        self.assertEqual(self._free(), 100)

    # ------------------------------------------------------------ Booking Form
    def _booking(self, qty, user=None):
        return self.env["mst.sale.booking"].with_user(user or self.env.user).create({
            "partner_id": self.partner.id,
            "warehouse_id": self.wh.id,
            "line_ids": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
        })

    def test_booking_form_number(self):
        self.assertTrue(self._booking(1).name.startswith("RM/"))

    def test_booking_form_approval(self):
        booking = self._booking(10, user=self.sales)
        booking.action_confirm()
        self.assertEqual(booking.state, "to_approve")
        self.assertEqual(self._free(), 100)
        with self.assertRaises(UserError):
            booking.action_approve()
        booking.with_env(self.env).action_approve()
        self.assertEqual(booking.state, "booked")
        self.assertTrue(booking.booking_approval_date)
        self.assertEqual(self._free(), 90)

    def test_booking_form_used_by_orders(self):
        booking = self._booking(60)
        booking.action_confirm()
        first = self._order(50, booking=booking)
        first.action_confirm()
        line = booking.line_ids
        self.assertEqual((line.used_qty, line.remaining_qty), (50, 10))
        self.assertEqual(line.move_id.quantity, 10)
        self.assertEqual(first.picking_ids.move_ids.quantity, 50)
        self.assertEqual(booking.state, "booked")
        second = self._order(10, booking=booking)
        second.action_confirm()
        self.assertEqual(booking.state, "closed")
        self.assertEqual(booking.sale_count, 2)

    def test_booking_form_release_closes_and_clears_quotation(self):
        booking = self._booking(60)
        booking.action_confirm()
        quotation = self._order(10, booking=booking)
        booking.action_release()
        self.assertEqual((booking.state, booking.line_ids.released_qty), ("closed", 60))
        self.assertFalse(quotation.booking_id)
        self.assertEqual(self._free(), 100)

    def test_booking_form_expiry(self):
        booking = self._booking(20)
        booking.action_confirm()
        booking.expiry_date = fields.Date.today() - timedelta(days=1)
        self.env["mst.sale.booking"]._cron_release_expired()
        self.assertEqual(booking.state, "closed")
        self.assertEqual(self._free(), 100)

    # ------------------------------------------------------------ Sales Journey
    def test_journey_shows_booking(self):
        Line = self.env["sale.order.line"]
        if not hasattr(Line, "_journey_booking_step"):
            self.skipTest("mst17_sales_journey is not installed")
        order = self._order(30)
        self.assertIsNone(order.order_line._journey_booking_step())
        order.action_book_stock()
        step = order.order_line._journey_booking_step()
        self.assertEqual((step["key"], step["title"]), ("booking_fg", "Finished Goods Booking"))
        self.assertIn(order.reservation_ids.name, step["info"])
        keys = [s["key"] for s in order.order_line._journey_steps(
            self.env["purchase.order.line"], 30, 0, 0, order.order_line._journey_invoice())]
        self.assertEqual(keys[:3], ["quotation", "booking_fg", "confirm"])

        greige = self._booking(5)
        greige.action_confirm()
        with_form = self._order(5, booking=greige)
        step = with_form.order_line._journey_booking_step()
        self.assertEqual((step["key"], step["title"]), ("booking_rm", "Greige Booking"))
