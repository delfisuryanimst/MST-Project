from datetime import date

from odoo import fields
from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install")
class TestStockCard(TransactionCase):
    """FDD Kartu Stok: example of appendix D on a new product, so the database data is not counted."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.tz = "Asia/Jakarta"
        cls.wh = env.ref("stock.warehouse0")
        cls.wh.reception_steps = "two_steps"
        cls.input = cls.wh.wh_input_stock_loc_id
        Location = env["stock.location"]
        cls.l0100 = Location.create({"name": "L0100", "location_id": cls.wh.lot_stock_id.id})
        cls.rack = Location.create({"name": "R01", "location_id": cls.l0100.id})
        cls.l01000 = Location.create({"name": "L01000", "location_id": cls.wh.lot_stock_id.id})
        cls.l0700 = Location.create({"name": "L0700", "location_id": cls.wh.lot_stock_id.id})
        cls.product = env["product.product"].create({"name": "Grg 03340000", "type": "product"})
        cls.adjust = cls.product.property_stock_inventory
        cls.vendor = env["res.partner"].create({"name": "Vendor 07"})
        customers = env.ref("stock.stock_location_customers")

        cls._move(cls.adjust, cls.l0100, 23809.15, "2019-01-12 02:00:00", "Beginning Balance")
        cls._move(cls.l0100, customers, 1550, "2019-06-09 02:00:00", "WH/RES/00302")
        cls.receipt = cls._move(env.ref("stock.stock_location_suppliers"), cls.input, 1599.29,
                                "2019-06-17 02:00:00", "Receipt", cls.vendor)
        cls._move(cls.input, cls.l0700, 1599.29, "2019-06-17 03:00:00", "WH/QCP/04675")
        cls._move(cls.l0100, cls.adjust, 433, "2019-06-22 02:00:00", "SMO17790")
        cls._move(cls.adjust, cls.l0100, 433, "2019-06-22 03:00:00", "SMO17790")
        cls._move(cls.l0100, cls.rack, 300, "2019-06-25 02:00:00", "MOVE1")
        cls._move(cls.adjust, cls.l01000, 999, "2019-06-26 02:00:00", "SIMILAR")

    @classmethod
    def _move(cls, src, dst, qty, when, name, partner=None):
        picking = partner and cls.env["stock.picking"].create({
            "partner_id": partner.id, "picking_type_id": cls.wh.in_type_id.id,
            "location_id": src.id, "location_dest_id": dst.id})
        move = cls.env["stock.move"].create({
            "name": name, "product_id": cls.product.id, "product_uom": cls.product.uom_id.id,
            "product_uom_qty": qty, "location_id": src.id, "location_dest_id": dst.id,
            "picking_id": picking and picking.id})
        move._action_confirm()
        move.quantity = qty
        move.picked = True
        move._action_done()
        move.move_line_ids.date = fields.Datetime.to_datetime(when)
        return picking or move

    def _card(self, date_from=date(2019, 1, 1), **filters):
        card = self.env["mst.stock.card"].create({
            "product_id": self.product.id, "date_from": date_from, "date_to": date(2019, 12, 31), **filters})
        card.action_show()
        return card, [(line.reference, line.move_type, round(line.quantity, 2), round(line.balance, 2))
                      for line in card.line_ids]

    def _quant(self, location):
        return sum(self.env["stock.quant"].search(
            [("product_id", "=", self.product.id), ("location_id", "child_of", location.id)]).mapped("quantity"))

    def test_location_card(self):
        """R-01/R-05/R-06: Location L0100 with its rack, L01000 is not read, last balance = stock."""
        card, rows = self._card(location_id=self.l0100.id)
        self.assertEqual(rows, [
            ("Saldo Awal", False, 0, 0),
            ("Beginning Balance", "in", 23809.15, 23809.15),
            ("WH/RES/00302", "out", 1550, 22259.15),
            ("SMO17790", "out", 433, 21826.15),
            ("SMO17790", "in", 433, 22259.15),
            ("MOVE1", "trf", 300, 22259.15),
        ])
        self.assertAlmostEqual(card.line_ids[-1].balance, self._quant(self.l0100))

    def test_rack_card(self):
        """R-04: a chosen rack shows only its own moves."""
        _card, rows = self._card(location_id=self.l0100.id, rack_id=self.rack.id)
        self.assertEqual(rows, [("Saldo Awal", False, 0, 0), ("MOVE1", "in", 300, 300)])

    def test_warehouse_card(self):
        """Appendix D without location: Vendor -> Input = IN with vendor name, Input -> L0700 = TRF."""
        card, rows = self._card(warehouse_id=self.wh.id)
        self.assertIn((self.receipt.name, "in", 1599.29, 23858.44), rows)
        self.assertIn(("WH/QCP/04675", "trf", 1599.29, 23858.44), rows)
        lines = {line.reference: line for line in card.line_ids}
        self.assertEqual(lines[self.receipt.name].partner_id, self.vendor)
        self.assertFalse(lines["WH/QCP/04675"].partner_id)
        self.assertAlmostEqual(card.line_ids[-1].balance, self._quant(self.wh.view_location_id))

    def test_opening_balance(self):
        """R-06: Saldo Awal = IN - OUT before the start date."""
        card, rows = self._card(date_from=date(2019, 6, 10), location_id=self.l0100.id)
        self.assertAlmostEqual(card.opening, 22259.15)
        self.assertEqual(rows[0], ("Saldo Awal", False, 0, 22259.15))
        self.assertEqual(rows[1], ("SMO17790", "out", 433, 21826.15))

    def test_location_choices(self):
        """R-03: Location lists level 3 only (no rack, no WH/Input); R-02: changing warehouse resets it."""
        card = self.env["mst.stock.card"].new({"product_id": self.product.id, "warehouse_id": self.wh.id})
        allowed = card.allowed_location_ids._origin
        self.assertTrue({self.l0100, self.l01000, self.l0700} <= set(allowed))
        self.assertNotIn(self.rack, allowed)
        self.assertNotIn(self.input, allowed)
        card.location_id = self.l0100
        card.rack_id = self.rack
        card._onchange_scope()
        self.assertEqual((card.location_id, card.rack_id), (self.l0100, self.rack))
        card.location_id = self.l0700
        card._onchange_scope()
        self.assertEqual((card.location_id, card.rack_id), (self.l0700, card.rack_id.browse()))
        card.warehouse_id = self.env["stock.warehouse"].create({"name": "WH 2", "code": "WH2"})
        card._onchange_scope()
        self.assertFalse(card.location_id)
