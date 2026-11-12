from datetime import timedelta

from odoo import Command
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestReceiptMonitor(TransactionCase):
    """Rules of the Monitor Penerimaan. Only the receipts made here are checked (or the change of the
    totals), so the test works on a database that already has receipts."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.Line = cls.env["mst.receipt.monitor.line"]
        cls.day, cls.start, _end = cls.Line._get_day(False)
        cls.in_type = cls.env.ref("stock.picking_type_in")
        cls.src = cls.env.ref("stock.stock_location_suppliers")
        cls.dest = cls.in_type.default_location_dest_id or cls.env.ref("stock.warehouse0").lot_stock_id
        cls.vendor = cls.env["res.partner"].create({"name": "Vendor Monitor"})

        def product(name, uom, tracking="none"):
            uom = cls.env.ref(uom)
            return cls.env["product.product"].create({
                "name": name, "type": "product", "tracking": tracking, "uom_id": uom.id, "uom_po_id": uom.id})

        cls.fabric = product("Fabric yd", "uom.product_uom_yard")
        cls.linen = product("Linen m", "uom.product_uom_meter")
        cls.yarn = product("Yarn kg", "uom.product_uom_kgm")
        cls.rolls = product("Fabric roll", "uom.product_uom_yard", "lot")

    def _receipt(self, product=None, qty=100, day=0, done_day=None, state=None):
        """A receipt scheduled `day` days from today (10:00 WIB), done on `done_day` when given."""
        product = product or self.fabric
        picking = self.env["stock.picking"].create({
            "picking_type_id": self.in_type.id, "partner_id": self.vendor.id,
            "location_id": self.src.id, "location_dest_id": self.dest.id,
            "move_ids": [Command.create({
                "name": product.name, "product_id": product.id, "product_uom_qty": qty,
                "product_uom": product.uom_id.id, "location_id": self.src.id, "location_dest_id": self.dest.id})],
        })
        if state == "draft":
            return picking
        picking.action_confirm()
        picking.scheduled_date = self.start + timedelta(days=day, hours=3)
        if state == "cancel":
            picking.action_cancel()
        elif done_day is not None:
            picking.move_ids.quantity = qty
            picking.move_ids.picked = True
            picking._action_done()
            picking.date_done = self.start + timedelta(days=done_day, hours=5)
        return picking

    def _rows(self):
        return {row["picking_id"]: row for row in self.Line._get_rows()[1]}

    def test_groups(self):
        """Diterima, Belum diterima, Tertunda diterima hari ini, Terlambat; other receipts are left out."""
        pickings = {
            "received": self._receipt(done_day=0),
            "pending": self._receipt(),
            "late_received": self._receipt(day=-3, done_day=0),
            "late": self._receipt(day=-3),
        }
        done_before = self._receipt(day=-3, done_day=-1)
        draft, cancel = self._receipt(state="draft"), self._receipt(state="cancel")
        rows = self._rows()
        for group, picking in pickings.items():
            self.assertEqual(rows[picking.id]["group"], group)
        for picking in (done_before, draft, cancel):
            self.assertNotIn(picking.id, rows)

    def test_ages(self):
        expected = {-2: "1_3", -5: "4_7", -10: "8_14", -20: "15", 0: "today"}
        pickings = {day: self._receipt(day=day) for day in expected}
        rows = self._rows()
        for day, age in expected.items():
            self.assertEqual(rows[pickings[day].id]["age"], age)
            self.assertEqual(rows[pickings[day].id]["age_days"], max(-day, 0))

    def test_units(self):
        """m becomes yds, kg stays apart, rolls are move lines with a lot; units are never added up."""
        linen = self._receipt(self.linen, 100)
        yarn = self._receipt(self.yarn, 20)
        rolls = self._receipt(self.rolls, 300)
        move = rolls.move_ids
        move.move_line_ids = [Command.create({
            "product_id": self.rolls.id, "picking_id": rolls.id, "lot_name": "LOT-%s" % i, "quantity": 100,
            "product_uom_id": self.rolls.uom_id.id, "location_id": move.location_id.id,
            "location_dest_id": move.location_dest_id.id}) for i in range(3)]
        rows = self._rows()
        self.assertAlmostEqual(rows[linen.id]["yds"], 109.3613, places=3)
        self.assertEqual((rows[linen.id]["kg"], rows[yarn.id]["yds"], rows[yarn.id]["kg"]), (0, 0, 20))
        self.assertEqual(rows[rolls.id]["roll"], 3)

    def test_summary_and_list(self):
        """The summary grows by the new receipts, and the opened list has the same documents."""
        before = self.Line.get_monitor_data()
        self._receipt(day=-5)
        self._receipt(day=-5)
        self._receipt(day=-20)
        after = self.Line.get_monitor_data()
        late = {g["key"]: g["doc"] for g in after["groups"]}["late"] - \
            {g["key"]: g["doc"] for g in before["groups"]}["late"]
        self.assertEqual(late, 3)
        ages = {a["key"]: a["doc"] for a in after["ages"]}
        ages_before = {a["key"]: a["doc"] for a in before["ages"]}
        self.assertEqual(ages["4_7"] - ages_before["4_7"], 2)
        action = self.Line.action_open_group(after["date"], "late", "4_7")
        lines = self.Line.search(action["domain"])
        self.assertEqual(len(lines), ages["4_7"])
        self.assertTrue(all(line.age == "4_7" and line.group == "late" for line in lines))

    def test_search_text(self):
        picking = self._receipt(day=-2)
        rows = self.Line._get_rows()[1]
        found = self.Line._filter_rows(rows, search=picking.name)
        self.assertEqual([row["picking_id"] for row in found], [picking.id])
        self.assertEqual(self.Line._filter_rows(rows, search="Vendor Monitor")[0]["partner_id"], self.vendor.id)
