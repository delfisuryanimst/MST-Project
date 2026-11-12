from datetime import timedelta
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestDeliveryMonitor(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.Line = env["mst.delivery.monitor.line"]
        _day, cls.start, _end = env["mst.receipt.monitor.line"]._get_day(False)
        cls.wh = env.ref("stock.warehouse0")
        cls.stock = cls.wh.lot_stock_id
        cls.customers = env.ref("stock.stock_location_customers")

        def location(name, parent=cls.stock, role=False):
            return env["stock.location"].create({"name": name, "location_id": parent.id, "delivery_monitor_role": role})

        cls.l0100 = location("L0100")
        cls.l0100_bin = location("A.R01", cls.l0100)
        cls.qc = location("QC0500", role="qc")
        cls.sam = location("SAM0500", role="sample")
        cls.ttp = location("TTP-1", role="excluded")
        cls.mtx = location("MTX Stock", env.ref("stock.stock_location_locations"), "agent")

        # Own types with manual reservation: documents stay Waiting until assigned, and the data of the
        # database is not counted.
        Type = env["stock.picking.type"]
        Type.search([("delivery_monitor_kind", "!=", False)]).delivery_monitor_kind = False
        cls.out_type = Type.create({
            "name": "Test Delivery", "sequence_code": "TOUT", "code": "outgoing", "warehouse_id": cls.wh.id,
            "default_location_src_id": cls.stock.id, "default_location_dest_id": cls.customers.id,
            "reservation_method": "manual", "delivery_monitor_kind": "customer"})
        cls.mtx_type = Type.create({
            "name": "MST2MTX", "sequence_code": "MTXT", "code": "internal", "warehouse_id": cls.wh.id,
            "default_location_src_id": cls.stock.id, "default_location_dest_id": cls.mtx.id,
            "reservation_method": "manual", "delivery_monitor_kind": "agent"})

        denim = env["product.category"].create({"name": "Denim", "delivery_monitor_group": "denim"})
        cls.denim_child = env["product.category"].create({"name": "Denim Sub", "parent_id": denim.id})
        cls.other = env["product.category"].create({"name": "Other"})
        cls.partner = env["res.partner"].create({"name": "Customer 07"})

    def _product(self, name, uom="uom.product_uom_yard", categ=None):
        uom = self.env.ref(uom)
        return self.env["product.product"].create({
            "name": name, "type": "product", "tracking": "lot", "uom_id": uom.id, "uom_po_id": uom.id,
            "categ_id": (categ or self.other).id})

    def _quant(self, product, location, qty, lot_name):
        lot = self.env["stock.lot"].create({"name": lot_name, "product_id": product.id})
        self.env["stock.quant"]._update_available_quantity(product, location, qty, lot_id=lot)
        return lot

    def _picking(self, product, qty, hours=3, star=True, note=False, ptype=None, assign=False, done_hours=False):
        ptype = ptype or self.out_type
        dest = ptype.default_location_dest_id if ptype == self.mtx_type else self.customers
        picking = self.env["stock.picking"].create({
            "picking_type_id": ptype.id, "location_id": self.stock.id, "location_dest_id": dest.id,
            "partner_id": self.partner.id, "priority": "1" if star else "0", "note": note,
            "move_ids": [(0, 0, {"name": product.name, "product_id": product.id, "product_uom_qty": qty,
                                 "product_uom": product.uom_id.id, "location_id": self.stock.id,
                                 "location_dest_id": dest.id})],
        })
        picking.action_confirm()
        picking.scheduled_date = self.start + timedelta(hours=hours)
        if assign or done_hours is not False:
            picking.action_assign()
        if done_hours is not False:
            picking.move_ids.picked = True
            picking._action_done()
            picking.date_done = self.start + timedelta(hours=done_hours)
        return picking

    def _data(self, period="today", scope="operational", category="all"):
        return self.Line.get_monitor_data(False, period, scope, category)

    @staticmethod
    def _by_key(items):
        return {item["key"]: item for item in items}

    def test_status_star_and_sample(self):
        """T-01, T-04, T-05: stars, Qty Real Ready, Done without star, sample split from Operasional."""
        p = self._product("Fabric")
        self._quant(p, self.l0100, 5000, "R1")
        self._picking(p, 500)                                             # waiting
        self._picking(p, 400, assign=True)                                # ready
        self._picking(p, 300, assign=True, note="<p><b>QTY REAL | </b></p>")  # qty real ready
        self._picking(p, 200, done_hours=5)                               # done today
        self._picking(p, 100, star=False)                                 # no star: not listed
        self._picking(p, 50, note="<p>INV - SAMPLE</p>")                  # sample
        self._picking(p, 40, note="<p>KIRIM | ada titip sample milan</p>")  # not a sample
        self._picking(p, 60, star=False, ptype=self.mtx_type)             # WH/MTX: all
        statuses = self._by_key(self._data()["statuses"])
        self.assertEqual(statuses["waiting"]["doc"], 3)
        self.assertEqual(statuses["ready"]["doc"], 1)
        self.assertEqual(statuses["qty_real"]["doc"], 1)
        self.assertEqual(statuses["done"]["doc"], 1)
        kinds = self._by_key(self._data()["kinds"])
        self.assertNotIn("sample", kinds)
        self.assertEqual(kinds["agent"]["doc"], 1)
        sample = self._data(scope="sample")
        self.assertEqual(sample["count"], 1)
        self.assertEqual(sample["kinds"][0]["key"], "sample")
        self.assertEqual(self._data(scope="all")["count"], 7)

    def test_sample_from_sample_location(self):
        """T-05: small delivery whose rolls all come from SAM0500 is a sample even without a note."""
        p = self._product("Swatch")
        self._quant(p, self.sam, 30, "S1")
        picking = self._picking(p, 30, assign=True)
        self.assertEqual(picking.move_line_ids.location_id, self.sam)
        self.assertEqual(self._data(scope="sample")["count"], 1)

    def test_issues(self):
        """T-02, T-03: issue of Waiting documents from the free MST stock, QC0500 split by inspection."""
        available, in_qc, short, missing, booked = (self._product(n) for n in ("A", "B", "C", "D", "E"))
        self._quant(available, self.l0100_bin, 2640, "A1")
        inspected = self._quant(in_qc, self.qc, 600, "B1")
        self._quant(in_qc, self.qc, 900, "B2")
        self._quant(short, self.l0100, 300, "C1")
        for location, lot in ((self.sam, "D1"), (self.ttp, "D2"), (self.mtx, "D3")):
            self._quant(missing, location, 500, lot)
        self._quant(booked, self.l0100, 1000, "E1")
        self._picking(booked, 1000, assign=True)  # reserves the whole E stock
        docs = {
            "available": self._picking(available, 1800),
            "qc": self._picking(in_qc, 1200),
            "short": self._picking(short, 1000),
            "none": self._picking(missing, 900),
            "booked": self._picking(booked, 500),
        }
        with patch.object(type(self.Line), "_inspected_lots", lambda self, lots: {inspected.id}):
            data = self._data()
            issues = self._by_key(data["issues"])
            self.assertEqual(issues["available"]["doc"], 1)
            self.assertEqual(issues["qc"]["doc"], 1)
            self.assertEqual(issues["qc"]["note"], "Sudah inspect 600 · belum inspect 900")
            self.assertEqual(issues["short"]["doc"], 1)
            self.assertEqual(issues["none"]["doc"], 2)
            _day, rows = self.Line._get_rows(False, "today", "operational", "all")
        rows = {r["picking"]: r for r in rows}
        self.assertEqual(rows[docs["available"]]["free_stock"], "L0100 2.640")
        self.assertEqual(rows[docs["none"]]["free_stock"], "MTX 500 · koordinasi Agen/Kemitraan")
        self.assertEqual(rows[docs["booked"]]["issue"], "none")

    def test_late_age_and_late_done(self):
        """T-06: late documents by age; late ones done today in their own table, not in the day's statuses."""
        p = self._product("Late")
        self._quant(p, self.l0100, 1000, "L1")
        self._picking(p, 100, hours=-24 * 5)
        self._picking(p, 100, hours=-24 * 20)
        self._picking(p, 100, hours=-24 * 2, done_hours=4)
        ages = self._by_key(self._data("late")["ages"])
        self.assertEqual(ages["4_7"]["doc"], 1)
        self.assertEqual(ages["15"]["doc"], 1)
        today = self._data()
        self.assertEqual(today["late_done"]["doc"], 1)
        self.assertEqual(today["count"], 0)

    def test_next_days(self):
        p = self._product("Next")
        self._picking(p, 100, hours=24 + 3)
        self.assertEqual(self._data("d1")["count"], 1)
        self.assertEqual(self._data()["count"], 0)

    def test_units_and_category(self):
        """T-08: m becomes yds, kg stays apart; R-06: category from the parent, % Done in yds."""
        linen = self._product("Linen", "uom.product_uom_meter", self.denim_child)
        knit = self._product("Knit", "uom.product_uom_kgm")
        self._quant(linen, self.l0100, 1000, "M1")
        self._picking(linen, 100, done_hours=5)
        self._picking(linen, 100)
        self._picking(knit, 20)
        data = self._data()
        self.assertAlmostEqual(data["total"]["yds"], 2 * 109.3613, places=2)
        self.assertEqual(data["total"]["kg"], 20)
        categories = self._by_key(data["categories"])
        self.assertEqual(categories["denim"]["doc"], 2)
        self.assertEqual(categories["denim"]["done_pct"], 50)
        self.assertEqual(categories["non_denim"]["doc"], 1)

    def test_places_and_list(self):
        """R-07 and T-07: rolls per sub warehouse without TTP; the opened list matches the summary."""
        p = self._product("Roll")
        for i in range(3):
            self._quant(p, self.l0100_bin, 100, "P%s" % i)
        self._quant(p, self.ttp, 100, "T1")
        self._picking(p, 400, assign=True)
        self._picking(p, 50)
        data = self._data()
        self.assertEqual(data["places"], [{"key": "L0100", "label": "L0100", "doc": 1, "roll": 3}])
        action = self.Line.action_open_list(data["date"], "today", "operational", "all", "status", "waiting")
        lines = self.Line.search(action["domain"])
        self.assertEqual(len(lines), self._by_key(data["statuses"])["waiting"]["doc"])
        self.assertEqual(action["context"], {"search_default_group_issue": 1})
        self.assertEqual(lines.product_line_ids.product_id, p)
        everything = self.Line.search(self.Line.action_open_list(data["date"], "today", "operational", "all")["domain"])
        self.assertEqual(len(everything), data["total"]["doc"])
