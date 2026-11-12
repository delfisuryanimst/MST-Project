from datetime import date, timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from ..models.order_calculator_engine import DISCLAIMER, _fmt, _percentiles

TODAY = date(2026, 11, 12)
# FDD appendix B numbers, fixed here so a refresh of the parameter by the cron does not change the tests.
HIST = {
    "delay_po_local": {"n": 4544, "p25": -0.9, "p50": 1.6, "p90": 41.3},
    "delay_po_import": {"n": 448, "p25": -1.7, "p50": 13.6, "p90": 68.8},
    "lt_import": {"n": 448, "p25": 34.2, "p50": 81.8, "p90": 121.3},
    "greige": {"n": 78, "p25": 8, "p50": 14, "p90": 52},
    "qc_days": {"p25": 1, "p50": 4, "p90": 21},
    "ship_days": {"p25": 0, "p50": 1, "p90": 3},
    "development": {"LD": [7, 10], "SO": [14, 14], "HL": [21, 28]},
    "process": {"_all": {"n": 1605, "p25": 15, "p50": 25, "p90": 50},
                "P/D Reaktif": {"n": 344, "p25": 27, "p50": 35, "p90": 56}},
}


@tagged("post_install", "-at_install")
class TestOrderCalculator(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        cls.engine = env["order.calculator.engine"]
        cls.wh = env.ref("stock.warehouse0")
        cls.yard = env.ref("uom.product_uom_yard")
        cls.product = env["product.product"].create({
            "name": "Calc Test;1;58;1/1;Navy", "type": "product", "sale_ok": True,
            "uom_id": cls.yard.id, "uom_po_id": cls.yard.id})
        cls.has_allowed = "allowed_warehouse_ids" in env["res.users"]._fields
        if cls.has_allowed:
            env.user.allowed_warehouse_ids = cls.wh
        cls.sales = env["res.users"].with_context(no_reset_password=True).create({
            "login": "calc_test_sales", "name": "Calc Sales",
            "groups_id": [Command.set([env.ref("base.group_user").id, env.ref("sales_team.group_sale_salesman").id])]})
        if cls.has_allowed:
            cls.sales.allowed_warehouse_ids = cls.wh

    def _data(self, rack=0.0, qc=0.0, supply=(), demand=(), fresh=False, imported=False):
        """Input of the calculation without the database: supply/demand as (qty, day[, import])."""
        return {
            "product": self.product, "warehouses": self.wh, "today": TODAY, "uom": "yd", "fresh": fresh,
            "import": imported, "qc": qc, "qc_name": "WH/Input", "rack": rack,
            "supply": [{"qty": q, "day": d, "date": TODAY + timedelta(days=d), "kind": "PO", "import": i}
                       for q, d, *rest in supply for i in [bool(rest and rest[0])]],
            "demand": [{"qty": q, "day": d, "date": TODAY + timedelta(days=d), "sales": "S", "kind": "SO"}
                       for q, d in demand],
        }

    def _ready(self, data, qty):
        return {s: self.engine._stock_plan(data, HIST, qty, s)["ready_day"] for s in ("p25", "p50", "p90")}

    # ---------------------------------------------------------- formulas
    def test_percentiles_and_format(self):
        self.assertEqual(_percentiles([50, 1, 2, 4, 5, 7, 8, 10, 12, 15, 20]),
                         {"n": 11, "p25": 4.5, "p50": 8, "p90": 20})
        self.assertEqual(_fmt(58739.1), "58.739,1")
        self.assertEqual(_fmt(849.0), "849")

    def test_stock_from_qc(self):
        """Rack 849, QC 10.956, one late SO of 1: free now 848, complete when QC is done (1 / 4 / 21 days)."""
        data = self._data(rack=849, qc=10956, demand=[(1, -2)], supply=[(400, -7)])
        self.assertEqual(self.engine._stock_plan(data, HIST, 3000, "p50")["free_now"], 848)
        self.assertEqual(self._ready(data, 3000), {"p25": 1, "p50": 4, "p90": 21})

    def test_po_delay_and_floor(self):
        """Pokezone example: PO delay per scenario, and a later SO keeps stock that cannot be taken."""
        data = self._data(rack=3294.5, demand=[(100, -1), (1335, 4)], supply=[(6051, -2), (6300, 12)])
        ready = self._ready(data, 8000)
        self.assertEqual(ready["p25"], 13)
        self.assertAlmostEqual(ready["p50"], 17.6)
        self.assertAlmostEqual(ready["p90"], 74.3)
        later_order = self._data(rack=1000, demand=[(800, 10)])
        plan = self.engine._stock_plan(later_order, HIST, 500, "p50")
        self.assertEqual((plan["free_now"], plan["ready_day"]), (200, None))

    def test_old_po_not_counted(self):
        data = self._data(supply=[(500, -50)])  # planned 50 days ago: past the p90 delay (41.3)
        plan = self.engine._stock_plan(data, HIST, 100, "p50")
        self.assertEqual((len(plan["stale"]), plan["ready_day"]), (1, None))
        self.assertIn("sisa PO lama", self.engine._stock_values(data, HIST, 100, None, {})["warning"])

    def test_customer_date_status(self):
        """T-02: Cukup / Cukup with a note / only when on time / Belum cukup."""
        values = self.engine._stock_values
        self.assertEqual(values(self._data(rack=5000), HIST, 1000, 5, {})["alerts"][0][:2],
                         ("ok", "Cukup untuk dikirim 17 Nov"))
        noted = values(self._data(supply=[(1000, 2)]), HIST, 1000, 10, {})["alerts"][0]
        self.assertEqual(noted[0], "ok")
        self.assertIn("Kalau barang terlambat", noted[2])
        self.assertEqual(values(self._data(supply=[(1000, 2)]), HIST, 1000, 4, {})["alerts"][0][0], "warn")
        short = values(self._data(rack=1000), HIST, 3000, 5, {})
        self.assertEqual(short["alerts"][0][:2], ("bad", "Belum cukup untuk 17 Nov"))
        self.assertEqual(short["rows"][1]["cells"][2], "Kurang 2.000")

    def test_fresh(self):
        """T-03: changing the process or the greige changes the result; import uses the import lead time."""
        data = self._data(fresh=True)
        parts = self.engine._fresh_parts
        total = lambda opts: sum(days for _label, days in parts(data, HIST, opts, "p50"))  # noqa: E731
        base = total({"process": "_all", "greige": "no"})
        self.assertEqual(base, 14 + 25 + 4 + 1)
        self.assertEqual(total({"process": "P/D Reaktif", "greige": "no"}), base + 10)
        self.assertEqual(total({"process": "_all", "greige": "yes"}), base - 14)
        # Labdip 7-10 days: the middle 8.5 rounds half up to 9.
        self.assertEqual(total({"process": "_all", "greige": "no", "development": "LD"}), base + 9)
        imported = [label for label, _days in parts(self._data(fresh=True, imported=True), HIST, {}, "p50")]
        self.assertEqual(imported, ["PO impor → diterima", "QC", "Siapkan & kirim"])

    # ---------------------------------------------------------- database
    def test_rack_qc_and_roles(self):
        """Rack = Stock, QC = Input and QC sub-locations, excluded sub-locations are not counted."""
        Quant, Location = self.env["stock.quant"], self.env["stock.location"]
        stock = self.wh.lot_stock_id
        qc_sub = Location.create({"name": "QC0500", "location_id": stock.id, "order_calc_role": "qc"})
        sample = Location.create({"name": "SAM0500", "location_id": stock.id, "order_calc_role": "excluded"})
        for location, qty in ((stock, 100), (qc_sub, 20), (sample, 30), (self.wh.wh_input_stock_loc_id, 40)):
            Quant._update_available_quantity(self.product, location, qty)
        data = self.engine._product_data(self.product)
        self.assertEqual((data["rack"], data["qc"]), (100, 60))

    def test_sales_user_and_own_form(self):
        """T-07 and R-01: a sales user can calculate; each user has one form and sees only their own."""
        calc = self.env["order.calculator"].with_user(self.sales).create({})
        calc.write({"product_id": self.product.id, "qty": 100, "uom_id": self.env.ref("uom.product_uom_meter").id})
        self.assertEqual(calc.product_qty, 109.37)  # 100 m, rounded to the yard rounding (0.01)
        calc.action_compute()
        self.assertIn(DISCLAIMER, calc.result_html)
        self.assertTrue(calc.result_json["alerts"])
        own = self.env["order.calculator"].create({})
        with self.assertRaises(AccessError):
            own.with_user(self.sales).read(["qty"])
        if self.has_allowed:
            self.sales.allowed_warehouse_ids = False
            with self.assertRaises(UserError):
                self.engine.with_user(self.sales)._warehouses()

    def test_quotation_line_button(self):
        """T-06: the line button uses the line qty and the Delivery Date; the dialog opens the filled form."""
        order = self.env["sale.order"].create({
            "partner_id": self.env["res.partner"].create({"name": "Calc Customer"}).id,
            "commitment_date": fields.Datetime.now() + timedelta(days=10),
            "order_line": [Command.create({"product_id": self.product.id, "product_uom_qty": 250})]})
        action = order.order_line.action_order_calc_check()
        wizard = self.env[action["res_model"]].browse(action["res_id"])
        self.assertEqual(wizard.qty, 250)
        self.assertTrue(wizard.need_date)
        self.assertIn(DISCLAIMER, wizard.result_html)
        calc = self.env["order.calculator"].browse(wizard.action_open_calculator()["res_id"])
        self.assertEqual((calc.product_id, calc.qty, calc.mode, calc.need_date),
                         (self.product, 250, "date", wizard.need_date))

    def test_cron_keeps_manual_numbers(self):
        """R-08: the cron stamps the date and only refreshes the PO numbers."""
        before = self.engine._history()
        self.engine._cron_update_history()
        after = self.engine._history()
        self.assertEqual(after["updated"], fields.Date.to_string(fields.Date.context_today(self.engine)))
        for key in ("qc_days", "ship_days", "greige", "process", "development"):
            self.assertEqual(after[key], before[key])
