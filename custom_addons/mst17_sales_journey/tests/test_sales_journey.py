from datetime import timedelta

from odoo import Command, fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSalesJourney(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Line = env["sale.order.line"]
        cls.wh = env.ref("stock.warehouse0")
        cls.partner = env["res.partner"].create({"name": "Journey Customer"})
        cls.vendor = env["res.partner"].create({"name": "Journey Vendor"})
        cls.yard = env.ref("uom.product_uom_yard")
        cls.stock_product = cls._product("Journey Stock")
        mto = env.ref("stock.route_warehouse0_mto")
        mto.active = True
        cls.mto_product = cls._product("Journey MTO", tracking="lot", routes=[
            env.ref("purchase_stock.route_warehouse0_buy").id, mto.id])
        cls.sales = env["res.users"].with_context(no_reset_password=True).create({
            "login": "journey_sales", "name": "Journey Sales",
            "groups_id": [Command.set([env.ref("base.group_user").id, env.ref("sales_team.group_sale_salesman").id])]})

    @classmethod
    def _product(cls, name, tracking="none", routes=()):
        return cls.env["product.product"].create({
            "name": name, "type": "product", "tracking": tracking, "uom_id": cls.yard.id, "uom_po_id": cls.yard.id,
            "route_ids": [Command.set(list(routes))], "invoice_policy": "delivery",
            "seller_ids": [Command.create({"partner_id": cls.vendor.id, "price": 1})]})

    def _order(self, product, qty, confirm=True, commitment=None, user=None):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id, "user_id": (user or self.env.user).id, "commitment_date": commitment,
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": qty})]})
        if confirm:
            order.action_confirm()
        return order

    def _journey(self, order):
        return order.order_line._journey_build(self.Line._journey_settings())

    def _deliver(self, order, qty):
        picking = order.picking_ids.filtered(lambda p: p.state not in ("done", "cancel"))
        picking.move_ids.quantity = qty
        picking.move_ids.picked = True
        picking._action_done()  # a backorder is made for the rest

    # ------------------------------------------------------------ rules
    def test_conclusion(self):
        conclusion = self.Line._journey_conclusion
        self.assertEqual(conclusion(True, 100, 150, 0), ("ready", 0))
        self.assertEqual(conclusion(True, 100, 40, 60), ("waiting_qc", 0))
        self.assertEqual(conclusion(True, 100, 40, 20), ("short", 40))
        self.assertEqual(conclusion(False, 100, 0, 0), (False, 0))

    def test_quotation_and_cancel(self):
        quotation = self._order(self.stock_product, 10, confirm=False)
        data = self._journey(quotation)
        self.assertEqual((data["stage"], data["conclusion"]), ("quotation", False))
        self.assertEqual([s["status"] for s in data["steps"]][:2], ["current", "todo"])
        quotation._action_cancel()
        self.assertEqual(self._journey(quotation)["stage"], "cancel")

    def test_from_stock_ready_short_partial_done(self):
        """Sold from stock: reserved = on rack; then partly delivered, delivered, invoiced."""
        self.env["stock.quant"]._update_available_quantity(self.stock_product, self.wh.lot_stock_id, 500)
        ready = self._order(self.stock_product, 300)
        data = self._journey(ready)
        self.assertEqual((data["stage"], data["conclusion"], data["rack"]), ("ready", "ready", 300))
        self.assertTrue(data["from_stock"])
        self.assertEqual(next(s for s in data["steps"] if s["key"] == "arrival")["title"], "Reserved")

        short = self._order(self.stock_product, 400)  # only 200 left to reserve
        data = self._journey(short)
        self.assertEqual((data["conclusion"], data["short_qty"]), ("short", 200))

        self._deliver(ready, 100)
        self.assertEqual(self._journey(ready)["stage"], "partial")
        self._deliver(ready, 200)
        self.assertEqual(self._journey(ready)["stage"], "to_invoice")
        ready._create_invoices().action_post()
        data = self._journey(ready)
        self.assertEqual((data["stage"], data["invoice"]["state"], data["invoice"]["count"]), ("done", "full", 1))

    def test_overdue(self):
        order = self._order(self.stock_product, 50, commitment=fields.Datetime.now() - timedelta(days=2))
        self.assertTrue(self._journey(order)["overdue"])

    def test_make_to_order_production_and_qc(self):
        """Bought for the order: production until received, then the received lots in QC with their age."""
        order = self._order(self.mto_product, 300)
        purchase_line = order.order_line._journey_purchase_lines()
        self.assertEqual(len(purchase_line), 1)
        purchase_line.order_id.button_confirm()
        data = self._journey(order)
        self.assertEqual((data["stage"], data["conclusion"], data["short_qty"]), ("production", "short", 300))
        self.assertTrue(data["steps"][2]["info"].startswith("Expected"))

        receipt = purchase_line.order_id.picking_ids
        receipt.move_ids.move_line_ids.write({"lot_name": "J-ROLL-1", "quantity": 300})
        receipt.move_ids.picked = True
        receipt._action_done()
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_move_line SET date = %s WHERE move_id IN %s",
                            (fields.Datetime.now() - timedelta(days=20), tuple(receipt.move_ids.ids)))
        self.env.invalidate_all()
        # The received lots are where the receipt put them; call that location the QC location here.
        self.env["ir.config_parameter"].set_param(
            "mst17_sales_journey.qc_location_name", receipt.move_ids.location_dest_id.name)
        data = self._journey(order)
        self.assertEqual((data["stage"], data["conclusion"], data["qc_total"]), ("qc", "waiting_qc", 300))
        self.assertEqual((data["qc_age"], data["qc_batches"][0]["warning"]), (20, True))

    def test_shared_production(self):
        order = self._order(self.mto_product, 100)
        other = self._order(self.mto_product, 50)
        purchase_line = order.order_line._journey_purchase_lines()
        purchase_line.move_dest_ids |= other.order_line.move_ids
        data = self._journey(order)
        self.assertEqual(data["shared_orders"], [other.name])
        self.assertEqual(data["conclusion"], False)

    # ------------------------------------------------------- stored status
    def test_refresh_and_cron_domain(self):
        self.env["stock.quant"]._update_available_quantity(self.stock_product, self.wh.lot_stock_id, 100)
        order = self._order(self.stock_product, 100)
        line = order.order_line
        self.assertIn(line, self.Line.search(self.Line._journey_cron_domain()))
        line._journey_refresh()
        self.assertEqual((line.journey_stage, line.journey_conclusion), ("ready", "ready"))
        self.assertFalse(line._journey_has_changed({"journey_short_qty": 0.1 + 0.2 - 0.3}))
        self._deliver(order, 100)
        order._create_invoices().action_post()
        line._journey_refresh()
        self.assertEqual(line.journey_stage, "done")
        self.assertNotIn(line, self.Line.search(self.Line._journey_cron_domain()))

    def test_tab_for_sales_user(self):
        """The Journey tab is built for a sales user without purchase or stock rights."""
        order = self._order(self.mto_product, 80, user=self.sales)
        html = order.with_user(self.sales).journey_html
        self.assertIn("Journey MTO", html)
