import io
import zipfile
from datetime import date, datetime

from odoo import Command
from odoo.tests import TransactionCase, tagged

from ..models.sales_delivery_engine import _fmt, _gsm, _width

# A month with no other data, so the totals below come from this test only.
MARCH = date(2019, 3, 1)


@tagged("post_install", "-at_install")
class TestSalesDelivery(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.engine = env["sales.delivery.engine"]
        cls.sku = "sku_gsm_finish_id" in env["product.product"]._fields
        cls.partner = env["res.partner"].create({"name": "Customer DP"})
        users = env["res.users"].with_context(no_reset_password=True)

        def user(login, group):
            return users.create({"login": login, "name": login, "groups_id": [
                Command.set([env.ref("base.group_user").id, env.ref(group).id])]})

        cls.own = user("dp_test_own", "sales_team.group_sale_salesman")
        cls.lead = user("dp_test_lead", "sales_team.group_sale_salesman_all_leads")
        cls.other = user("dp_test_other", "sales_team.group_sale_salesman")
        cls.ppic = user("dp_test_ppic", "mst17_sales_delivery.group_delivery_all")
        cls.team = env["crm.team"].create({"name": "DP Team 1", "member_ids": [Command.set((cls.own | cls.lead).ids)]})
        cls.team2 = env["crm.team"].create({"name": "DP Team 2", "member_ids": [Command.set(cls.other.ids)]})
        cls.yarn_categ = env["product.category"].create({"name": "DP Benang", "delivery_yarn": True})

    # ------------------------------------------------------------- helpers
    def _product(self, uom="uom.product_uom_yard", categ=None, gsm=None, width=None, use=None):
        uom = self.env.ref(uom)
        values = {"name": "DP %s" % uom.name, "type": "consu", "uom_id": uom.id, "uom_po_id": uom.id}
        if categ:
            values["categ_id"] = categ.id
        if self.sku:
            Sku = self.env["sku.values"]
            values.update({
                "sku_gsm_finish_id": gsm and Sku.create({"name": gsm}).id,
                "sku_width_finish_id": width and Sku.create({"name": width}).id,
                "sku_pemakaian_id": use and Sku.create({"name": use}).id,
            })
        return self.env["product.product"].create(values)

    def _backdate(self, picking, when):
        self.env.flush_all()
        self.env.cr.execute("UPDATE stock_move SET date = %s WHERE picking_id = %s", (when, picking.id))
        self.env.cr.execute("UPDATE stock_picking SET date_done = %s WHERE id = %s", (when, picking.id))
        self.env.invalidate_all()

    def _validate(self, picking, when=datetime(2019, 3, 10, 3), qty=None):
        for move in picking.move_ids:
            move.quantity = move.product_uom_qty if qty is None else qty
            move.picked = True
        picking._action_done()
        self._backdate(picking, when)
        return picking

    def _deliver(self, product, qty, user=None, team=None, when=datetime(2019, 3, 10, 3), note=False):
        order = self.env["sale.order"].create({
            "partner_id": self.partner.id, "user_id": (user or self.own).id, "team_id": (team or self.team).id,
            "order_line": [Command.create({"product_id": product.id, "product_uom_qty": qty})]})
        order.action_confirm()
        picking = order.picking_ids
        picking.note = note
        return self._validate(picking, when)

    def _return(self, picking, qty, when=datetime(2019, 3, 20, 3), admin=False):
        wizard = self.env["stock.return.picking"].with_context(
            active_id=picking.id, active_model="stock.picking").create({})
        wizard.product_return_moves.write({"quantity": qty, "to_refund": True})
        back = self.env["stock.picking"].browse(wizard.create_returns()["res_id"])
        back.admin_return = admin
        return self._validate(back, when)

    def _replace(self, original, qty):
        picking = original.copy({"replacement_of_id": original.id})
        picking.move_ids.sale_line_id = original.move_ids.sale_line_id
        picking.action_confirm()
        return self._validate(picking, datetime(2019, 3, 25, 3), qty)

    def _march(self, user=None, scope=None):
        engine = self.engine.with_user(user) if user else self.engine
        return engine._lines(MARCH, date(2019, 3, 31), scope or {"all": True})

    # ------------------------------------------------------------- units
    def test_sku_text(self):
        self.assertAlmostEqual(_gsm("10 Oz"), 339.06, places=2)
        self.assertEqual(_gsm("110-130"), 110)
        self.assertEqual(_gsm("108,4"), 108.4)
        self.assertEqual(_gsm(""), 0)
        self.assertEqual(_width('44/45"'), 44)
        self.assertEqual(_width('58"'), 58)
        self.assertEqual(_fmt(1234.5), "1.234,5")
        self.assertEqual(_fmt(1000.0), "1.000")

    def test_converter(self):
        """m to yds, kg to yds with GSM and width, kg without them stays kg, yarn apart, Kain Kantong 50%."""
        meter = self._product("uom.product_uom_meter")
        yarn = self._product("uom.product_uom_kgm", self.yarn_categ)
        bare_kg = self._product("uom.product_uom_kgm")
        convert = self.engine._converter((meter | yarn | bare_kg).ids)
        self.assertEqual(convert(meter.id, meter.uom_id, 100)[0], "yds")
        self.assertAlmostEqual(convert(meter.id, meter.uom_id, 100)[1], 109.3613, places=3)
        self.assertEqual(convert(yarn.id, yarn.uom_id, 50)[:2], ("yarn_kg", 50))
        self.assertEqual(convert(bare_kg.id, bare_kg.uom_id, 30)[:2], ("kg", 30))
        if not self.sku:
            return
        knit = self._product("uom.product_uom_kgm", gsm="160", width='58/60"')
        pocket = self._product(use="Kain Kantong")
        convert = self.engine._converter((knit | pocket).ids)
        kind, value, _factor = convert(knit.id, knit.uom_id, 300)
        self.assertEqual(kind, "yds")
        self.assertAlmostEqual(value, 300 * 1000 / (160 * 58 * 0.0254) / 0.9144, places=3)
        self.assertEqual(convert(pocket.id, pocket.uom_id, 10)[2], 0.5)

    # -------------------------------------------------------------- lines
    def test_sent_returned_and_wib_day(self):
        """Deliveries and customer returns count on the WIB day of the move; netto = sent - returned."""
        fabric = self._product()
        sent = self._deliver(fabric, 1000)
        self._return(sent, 200)
        late_evening = self._deliver(fabric, 50, when=datetime(2019, 3, 31, 17, 30))  # 1 Apr 00:30 WIB
        total = self.engine._sum(self._march(), netto=True)[0]
        self.assertEqual((total["sent"], total["returned"], total["netto"]), (1000, 200, 800))
        april = self.engine._lines(date(2019, 4, 1), date(2019, 4, 30), {"all": True})
        self.assertEqual([line["picking_id"] for line in april], [late_evening.id])

    def test_scope(self):
        """R-11: Own Documents sees itself, All Documents its teams, PPIC everything."""
        fabric = self._product()
        mine = self._deliver(fabric, 10, self.own, self.team)
        lead = self._deliver(fabric, 20, self.lead, self.team)
        other = self._deliver(fabric, 30, self.other, self.team2)
        seen = {}
        for user in (self.own, self.lead, self.ppic):
            scope = self.engine.with_user(user)._scope()
            seen[user] = {line["picking_id"] for line in self._march(user, scope)}
        self.assertEqual(seen[self.own], {mine.id})
        self.assertEqual(seen[self.lead], {mine.id, lead.id})
        self.assertEqual(seen[self.ppic], {mine.id, lead.id, other.id})

    # ------------------------------------------------------- admin return
    def test_admin_return_chains(self):
        """R-06: a non-qty revision counts the original, a qty revision the replacement, no replacement
        nothing; a transfer marked admin only (WH/OUT BARU) is not counted."""
        fabric = self._product()
        same = self._deliver(fabric, 1000)
        same_back = self._return(same, 1000, admin=True)
        same_new = self._replace(same, 1002)                    # 0.2% difference
        qty = self._deliver(fabric, 1000)
        self._return(qty, 1000, admin=True)
        qty_new = self._replace(qty, 900)
        none = self._deliver(fabric, 500)
        none_back = self._return(none, 500, admin=True)
        admin_only = self._deliver(fabric, 300)
        admin_only.admin_return = True
        excluded = {p: p.delivery_excluded for p in (same, same_back, same_new, qty, qty_new, none, none_back, admin_only)}
        self.assertEqual(excluded, {same: False, same_back: True, same_new: True, qty: True, qty_new: False,
                                    none: True, none_back: True, admin_only: True})
        counted = {line["picking"] for line in self._march()}
        names = {p.name: label for label, p in (("same", same), ("same_new", same_new), ("qty", qty),
                                                ("qty_new", qty_new), ("none", none), ("admin_only", admin_only))}
        self.assertEqual(counted, {same.name, qty_new.name}, names)

    def test_init_from_notes(self):
        """Install hook: admin returns and replacements are read from the old note texts."""
        fabric = self._product()
        original = self._deliver(fabric, 400, note="<p>RETUR ADMIN | salah lokasi</p>")
        self._return(original, 400)
        replacement = self._deliver(fabric, 400, note="<p>Pengganti Ex.&nbsp;%s</p>" % original.name)
        self.env["stock.picking"]._init_admin_return_from_notes()
        self.assertTrue(original.admin_return)
        self.assertEqual(replacement.replacement_of_id, original)
        self.assertFalse(original.delivery_excluded)
        self.assertTrue(replacement.delivery_excluded)

    # -------------------------------------------------------------- report
    def test_report_and_export(self):
        fabric = self._product()
        self._deliver(fabric, 1000)
        sent = self._deliver(fabric, 500, when=datetime(2019, 2, 10, 3))
        self._return(sent, 100, when=datetime(2019, 3, 5, 3))
        data = self.engine.with_user(self.ppic)._report("month", MARCH, False, measure="netto", details=True)
        self.assertEqual(data["cards"][0]["value"], "900 yds")
        self.assertEqual(data["cards"][0]["change"]["trend"], "up")  # 900 vs 500 in February
        self.assertEqual([m["label"] for m in data["chart"]], ["Okt", "Nov", "Des", "Jan", "Feb", "Mar"])
        content = self.engine._xlsx(data, [("Periode", data["label"])])
        sheets = zipfile.ZipFile(io.BytesIO(content)).namelist()
        self.assertEqual(len([s for s in sheets if s.startswith("xl/worksheets/sheet")]), 8)
