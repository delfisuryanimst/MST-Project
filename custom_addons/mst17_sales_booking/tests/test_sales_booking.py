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

    def _booking(self, qty, user=None):
        return self.env["mst.sale.booking"].with_user(user or self.env.user).create({
            "partner_id": self.partner.id,
            "warehouse_id": self.wh.id,
            "line_ids": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
        })

    def _order(self, qty, partner=None, booking=None):
        return self.env["sale.order"].create({
            "partner_id": (partner or self.partner).id,
            "booking_id": booking and booking.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": qty})],
        })

    def _free(self):
        return self.product.with_context(location=self.wh.lot_stock_id.id).free_qty

    def test_manager_books_directly_and_holds_stock(self):
        booking = self._booking(60)
        self.assertTrue(booking.name.startswith("BOOK/"))
        booking.action_confirm()
        self.assertEqual(booking.state, "booked")
        self.assertEqual(booking.line_ids.move_id.state, "assigned")
        self.assertEqual(self._free(), 40)
        # Another customer cannot take the booked stock.
        other = self._order(50, partner=self.other)
        other.action_confirm()
        self.assertEqual(other.picking_ids.move_ids.quantity, 40)

    def test_salesperson_needs_approval(self):
        booking = self._booking(10, user=self.sales)
        booking.action_confirm()
        self.assertEqual(booking.state, "to_approve")
        self.assertEqual(self._free(), 100)
        with self.assertRaises(UserError):
            booking.action_approve()
        booking.with_env(self.env).action_approve()
        self.assertEqual(booking.state, "booked")
        self.assertEqual(self._free(), 90)

    def test_not_enough_stock(self):
        with self.assertRaises(UserError):
            self._booking(150).action_confirm()

    def test_order_uses_part_of_booking(self):
        booking = self._booking(60)
        booking.action_confirm()
        order = self._order(50, booking=booking)
        order.action_confirm()
        line = booking.line_ids
        self.assertEqual((line.utilized_qty, line.remaining_qty), (50, 10))
        self.assertEqual(line.move_id.quantity, 10)
        self.assertEqual(order.picking_ids.move_ids.quantity, 50)
        self.assertEqual(booking.state, "booked")
        self.assertEqual(self._free(), 40)

    def test_order_uses_whole_booking(self):
        booking = self._booking(60)
        booking.action_confirm()
        order = self._order(60, booking=booking)
        order.action_confirm()
        self.assertEqual(booking.state, "closed")
        self.assertEqual(booking.line_ids.move_id.state, "cancel")
        self.assertEqual(order.picking_ids.move_ids.quantity, 60)
        self.assertEqual(booking.sale_count, 1)

    def test_book_stock_from_quotation(self):
        order = self._order(30)
        order.action_book_stock()
        self.assertEqual(order.booking_id.state, "booked")
        self.assertEqual(order.booking_id.line_ids.product_uom_qty, 30)
        self.assertEqual(self._free(), 70)
        order.action_confirm()
        self.assertEqual(order.booking_id.state, "closed")
        self.assertEqual(order.picking_ids.move_ids.quantity, 30)

    def test_release_and_expiry(self):
        booking = self._booking(60)
        booking.action_confirm()
        booking.action_release()
        self.assertEqual((booking.state, booking.line_ids.released_qty), ("released", 60))
        self.assertEqual(self._free(), 100)

        expired = self._booking(20)
        expired.action_confirm()
        expired.expiry_date = fields.Date.today() - timedelta(days=1)
        self.env["mst.sale.booking"]._cron_release_expired()
        self.assertEqual(expired.state, "released")
        self.assertEqual(self._free(), 100)

    def test_journey_shows_booking(self):
        Line = self.env["sale.order.line"]
        if not hasattr(Line, "_journey_booking_step"):
            self.skipTest("mst17_sales_journey is not installed")
        order = self._order(30)
        self.assertIsNone(order.order_line._journey_booking_step())
        order.action_book_stock()
        step = order.order_line._journey_booking_step()
        self.assertEqual(step["key"], "booking_fg")
        self.assertTrue(step["done"])
        self.assertIn(order.booking_id.name, step["info"])
        keys = [s["key"] for s in order.order_line._journey_steps(
            self.env["purchase.order.line"], 30, 0, 0, order.order_line._journey_invoice())]
        self.assertEqual(keys[:3], ["quotation", "booking_fg", "confirm"])

    def test_greige_sequence(self):
        booking = self.env["mst.sale.booking"].create({
            "partner_id": self.partner.id, "inventory_type": "rm",
            "line_ids": [Command.create({"product_id": self.product.id, "product_uom_qty": 1})]})
        self.assertTrue(booking.name.startswith("RM/"))
