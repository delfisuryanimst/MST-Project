from odoo import Command
from odoo.exceptions import AccessError, UserError
from odoo.tests import Form, TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestSampleRequest(TransactionCase):
    """FDD Permintaan Sampel R-03..R-12."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.ref("base.user_admin").notification_type = "inbox"  # the test database has no mail server
        wh = env.ref("stock.warehouse0")
        Location = env["stock.location"]
        virtual = env.ref("stock.stock_location_locations_virtual")
        cls.loc_mst = Location.create({"name": "Pemakaian Sample MST", "usage": "inventory", "location_id": virtual.id})
        cls.loc_sl = Location.create({"name": "Pemakaian Sample Agen SL", "usage": "inventory", "location_id": virtual.id})
        cls.loc_mtx = Location.create({"name": "Pemakaian Sample Agen MTX", "usage": "inventory", "location_id": virtual.id})
        cls.l0500 = Location.create({"name": "T-L0500", "location_id": wh.lot_stock_id.id, "sample_source": True})
        cls.sam0500 = Location.create({"name": "T-SAM0500", "location_id": wh.lot_stock_id.id, "sample_source": True})
        env.company.write({"sample_location_id": cls.loc_mst.id, "sample_picking_type_id": wh.int_type_id.id})
        Team = env["crm.team"]
        cls.team_sl = Team.create({"name": "SL", "sample_location_id": cls.loc_sl.id})
        cls.team_mls = Team.create({"name": "MLS", "sample_location_id": cls.loc_mtx.id})
        cls.team_mst = Team.create({"name": "MST-C"})

        def user(login, groups, team=None):
            new = env["res.users"].create({
                "name": login, "login": login, "email": "%s@example.com" % login, "notification_type": "inbox",
                "groups_id": [Command.set([env.ref(g).id for g in groups])]})
            if team:
                env["crm.team.member"].create({"crm_team_id": team.id, "user_id": new.id})
            return new

        sales = "mst17_sample_request.group_sample_sales"
        cls.sales_a = user("spl_sales_a", [sales], cls.team_mst)
        cls.sales_sl = user("spl_sales_sl", [sales], cls.team_sl)
        cls.sales_mls = user("spl_sales_mls", [sales], cls.team_mls)
        cls.sales_none = user("spl_sales_none", [sales])
        cls.admin_sales = user("spl_admin_sales", ["mst17_sample_request.group_sample_admin_sales"])
        cls.cutter = user("spl_cutter", ["mst17_sample_request.group_sample_cutter", "stock.group_stock_user"])
        cls.customer = env["res.partner"].create({"name": "Customer 07"})
        cls.product = env["product.product"].create({"name": "P/D Dastin; Dk. Grey", "type": "product"})
        Quant = env["stock.quant"]
        Quant._update_available_quantity(cls.product, cls.l0500, 412.5)
        Quant._update_available_quantity(cls.product, cls.sam0500, 38.0)

    def _request(self, user, sample_type="free", price=0.0, qty=3.0, **vals):
        """Created by the user; returned with the test's rights so the checks can read every document."""
        return self.env["mst.sample.request"].with_user(user).create(dict({
            "partner_id": self.customer.id,
            "sample_type": sample_type,
            "line_ids": [Command.create({"product_id": self.product.id, "qty": qty, "price_unit": price})],
        }, **vals)).with_env(self.env)

    def _submit(self, request):
        request.with_user(request.user_id).action_submit()

    def test_destination_from_team(self):
        """R-05: SL -> Agen SL, MLS -> Agen MTX, other team or no team -> MST."""
        self.assertEqual(self._request(self.sales_sl).location_dest_id, self.loc_sl)
        self.assertEqual(self._request(self.sales_mls).location_dest_id, self.loc_mtx)
        self.assertEqual(self._request(self.sales_a).location_dest_id, self.loc_mst)
        self.assertEqual(self._request(self.sales_none).location_dest_id, self.loc_mst)

    def test_requester(self):
        """R-04: a sales user cannot file for someone else; Admin Sales can, and is recorded as the inputter."""
        with self.assertRaises(AccessError):
            self._request(self.sales_a, user_id=self.sales_sl.id)
        request = self._request(self.admin_sales, user_id=self.sales_sl.id)
        self.assertEqual((request.user_id, request.entry_uid), (self.sales_sl, self.admin_sales))
        self.assertEqual(request.location_dest_id, self.loc_sl)
        self.assertTrue(request.name.startswith("SPL/"))

    def test_paid_needs_price(self):
        """R-03: a paid request without a price cannot be submitted."""
        request = self._request(self.sales_a, "paid")
        with self.assertRaises(UserError):
            self._submit(request)
        request.line_ids.price_unit = 45000
        self._submit(request)
        self.assertEqual(request.state, "submitted")

    def test_submit_suggests_source_and_notifies(self):
        """R-06 / R-07: the source with the most free stock is proposed, the cutting admin gets an activity."""
        request = self._request(self.sales_a)
        self._submit(request)
        line = request.line_ids
        self.assertEqual((line.location_src_id, line.qty_cut, line.qty_free), (self.l0500, 3.0, 412.5))
        self.assertIn(self.cutter, request.activity_ids.user_id)

    def test_free_makes_transfer_then_done(self):
        """R-08: Proses makes the Internal Transfer; validating it closes the request."""
        request = self._request(self.sales_a)
        self._submit(request)
        wizard = self.env["mst.sample.request.process"].with_user(self.cutter).create({"request_id": request.id})
        self.assertIn("Internal Transfer", wizard.preview)
        wizard.action_confirm()
        picking = request.picking_ids
        self.assertEqual(request.state, "processing")
        self.assertEqual((picking.location_id, picking.location_dest_id), (self.l0500, self.loc_mst))
        self.assertEqual((picking.partner_id, picking.origin, picking.user_id), (self.customer, request.name, self.cutter))
        self.assertEqual(request.line_ids.move_id, picking.move_ids)
        self.assertFalse(request.activity_ids)
        self.assertEqual(request.with_user(self.cutter).action_view_pickings()["res_id"], picking.id)
        Form(request.with_user(self.sales_a))  # the requester has no Inventory rights
        picking.move_ids.quantity = 3.0
        picking.move_ids.picked = True
        picking.button_validate()
        self.assertEqual(request.state, "done")
        self.assertTrue(request.date_done)

    def test_paid_makes_quotation_without_sales_rights(self):
        """R-09 / R-10: the cutting admin (no Sales group) makes the Quotation; confirming it closes the request."""
        self.assertFalse(self.cutter.has_group("sales_team.group_sale_salesman"))
        request = self._request(self.sales_a, "paid", price=45000, qty=5.0)
        self._submit(request)
        request.with_user(self.cutter)._process()
        order = request.sale_order_id
        self.assertEqual((order.partner_id, order.user_id, order.client_order_ref), (self.customer, self.sales_a, request.name))
        self.assertEqual((order.order_line.price_unit, order.order_line.product_uom_qty), (45000, 5.0))
        self.assertEqual(request.document_names, order.name)
        self.assertEqual(request.action_view_quotation()["res_id"], order.id)
        self.assertTrue(Form(request.with_user(self.cutter)).has_quotation)  # the cutter has no Sales rights
        order.action_confirm()
        self.assertEqual(request.state, "done")

    def test_reject_needs_reason(self):
        """R-06: Tolak needs a reason."""
        request = self._request(self.sales_a)
        self._submit(request)
        with self.assertRaises(UserError):
            request.with_user(self.cutter)._reject(" ")
        request.with_user(self.cutter)._reject("Stok L0500 habis, tunggu WH/IN")
        self.assertEqual(request.state, "rejected")

    def test_rights(self):
        """R-10: sales see only their own requests and cannot process."""
        own = self._request(self.sales_a)
        other = self._request(self.sales_sl)
        visible = self.env["mst.sample.request"].with_user(self.sales_a).search([("id", "in", (own | other).ids)])
        self.assertEqual(visible, own)
        self._submit(own)
        with self.assertRaises(AccessError):
            own.with_user(self.sales_a).action_open_process()
        self.assertEqual(len(self.env["mst.sample.request"].with_user(self.cutter).search(
            [("id", "in", (own | other).ids)])), 2)

    def test_report_conversion(self):
        """R-12: a confirmed SO of the same customer and product after the sample counts as converted."""
        request = self._request(self.sales_a)
        self._submit(request)
        request.with_user(self.cutter)._process()
        picking = request.picking_ids
        picking.move_ids.quantity = 3.0
        picking.move_ids.picked = True
        picking.button_validate()
        order = self.env["sale.order"].create({
            "partner_id": self.customer.id,
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": 500})]})
        order.action_confirm()
        self.env.flush_all()
        row = self.env["mst.sample.request.report"].search([("request_id", "=", request.id)])
        self.assertTrue(row.converted)
        self.assertEqual(row.qty_cut, 3.0)
