from odoo.exceptions import AccessError, UserError
from odoo.tests import TransactionCase, tagged

from odoo.addons.mst17_qc_roll.models.stock_lot import ROLL_ID, epc_v4


@tagged("post_install", "-at_install")
class TestQcRoll(TransactionCase):
    """FDD QC ID Roll R-01..R-12, with the rolls of mockup boards 7-9."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = cls.env
        env.user.groups_id |= env.ref("mst17_qc_roll.group_qc_koordinator")
        cls.wh = env.ref("stock.warehouse0")
        Location = env["stock.location"]
        cls.qc = Location.create({"name": "QC0500", "location_id": cls.wh.lot_stock_id.id})
        cls.l0500 = Location.create({"name": "L0500", "location_id": cls.wh.lot_stock_id.id})
        env.company.write({"mst_qc_location_id": cls.qc.id, "mst_qc_qty_tolerance": 5})
        Product = env["product.product"]
        cls.product = Product.create({"name": "83013022", "type": "product", "tracking": "lot"})
        cls.product_a2 = Product.create({"name": "83013025", "type": "product", "tracking": "lot"})
        cls.product_b = Product.create({"name": "99999002", "type": "product", "tracking": "lot"})
        cls.qcp = env["stock.picking"].create({
            "picking_type_id": cls.wh.int_type_id.id, "location_id": cls.l0500.id, "location_dest_id": cls.qc.id})
        cls.has_epc = "x_epc" in env["stock.lot"]._fields

    def _roll(self, qty, **lot_vals):
        lot = self.env["stock.lot"].create(dict(
            name=self.env["ir.sequence"].next_by_code("mst.roll.id"), product_id=self.product.id, **lot_vals))
        self.env["stock.quant"]._update_available_quantity(self.product, self.qc, qty, lot_id=lot)
        return lot

    def _inspection(self, lot, qty, **vals):
        return self.env["mst.qc.inspection"].create(dict(
            picking_id=self.qcp.id, lot_id=lot.id, location_id=self.qc.id, qty_result=qty, **vals))

    def _qty(self, lot):
        lot.invalidate_recordset(["product_qty"])
        return lot.product_qty

    def _events(self, lot):
        return lot.mst_history_ids.mapped("event")

    def test_roll_id_and_epc(self):
        """R-01: an ID Roll lot is marked and gets its EPC v4."""
        self.assertEqual(epc_v4("R26-000123"), "40000000000000000000000026000123")
        lot = self._roll(10)
        self.assertTrue(ROLL_ID.match(lot.name))
        self.assertTrue(lot.mst_is_roll_id)
        if self.has_epc:
            self.assertEqual(lot.x_epc, epc_v4(lot.name))

    def test_lot_warna_only(self):
        """R-06, board 7 row 1: only the field and the history change, no Simplified MO."""
        lot = self._roll(59.60, mst_lot_warna="01")
        ins = self._inspection(lot, 59.60, lot_warna_result="19")
        self.assertEqual((ins.result_type, ins.result_preview), ("data", "Lot Warna"))
        ins.action_validate_result()
        self.assertFalse(ins.conversion_ids)
        self.assertEqual(lot.mst_lot_warna, "19")
        self.assertAlmostEqual(self._qty(lot), 59.60)
        history = lot.mst_history_ids.filtered(lambda h: h.event == "lot_warna")
        self.assertEqual((history.old_value, history.new_value), ("01", "19"))

    def test_qty_correction_same_lot(self):
        """R-07, appendix D: 59,60 -> 51,00 on the same lot; above the tolerance a reason is needed."""
        lot = self._roll(59.60)
        ins = self._inspection(lot, 51.0)
        with self.assertRaises(UserError):
            ins.action_validate_result()
        ins.reason = "Short roll"
        ins.action_validate_result()
        self.assertEqual(ins.conversion_ids.conversion_type, "qty_adjust")
        self.assertEqual(ins.conversion_ids.produce_line_ids.lot_id, lot)
        self.assertAlmostEqual(self._qty(lot), 51.0)
        self.assertTrue(ins.alert_id)

    def test_product_change(self):
        """R-09, board 9: same lot name in the other product, the tag moves, a reason is needed."""
        lot = self._roll(88.0, mst_lot_warna="01", mst_k3l="K3L:21-D-000736")
        epc = self.has_epc and lot.x_epc
        ins = self._inspection(lot, 88.0, product_result_id=self.product_a2.id)
        self.assertEqual(ins.result_type, "product_change")
        with self.assertRaises(UserError):
            ins.action_validate_result()
        ins.reason = "Colour is 83013025"
        ins.action_validate_result()
        new_lot = ins.conversion_ids.produce_line_ids.lot_id
        self.assertEqual(ins.conversion_ids.conversion_type, "product_change")
        self.assertEqual((new_lot.name, new_lot.product_id), (lot.name, self.product_a2))
        self.assertEqual((new_lot.mst_k3l, new_lot.mst_lot_warna), ("K3L:21-D-000736", "01"))
        self.assertAlmostEqual(self._qty(new_lot), 88.0)
        self.assertAlmostEqual(self._qty(lot), 0.0)
        self.assertIn("ganti_produk", self._events(new_lot))
        self.assertEqual(ins.alert_id.result_type, "product_change")
        if self.has_epc:
            self.assertEqual((lot.x_epc, new_lot.x_epc), (False, epc))

    def test_downgrade_whole_roll_moves_tag(self):
        """R-10, board 7 row 5: 95,10 -> 40,00 B Grade; the A lot is empty, so the tag goes to the B lot."""
        lot = self._roll(95.10)
        epc = self.has_epc and lot.x_epc
        ins = self._inspection(lot, 0.0, qty_down=40.0, product_result_id=self.product_b.id, reason="Defects")
        ins.action_validate_result()
        lot_b = self.env["stock.lot"].search([("name", "=", lot.name), ("product_id", "=", self.product_b.id)])
        self.assertEqual(lot_b.mst_item_a_id, self.product)
        self.assertAlmostEqual(self._qty(lot_b), 40.0)
        self.assertEqual(set(ins.conversion_ids.mapped("conversion_type")), {"downgrade", "qty_adjust"})
        if self.has_epc:
            self.assertEqual((lot.x_epc, lot_b.x_epc), (False, epc))

    def test_partial_downgrade_keeps_tag(self):
        """R-10: while the A roll still has stock, its tag stays on it."""
        lot = self._roll(100.0)
        ins = self._inspection(lot, 70.0, qty_down=30.0, product_result_id=self.product_b.id)
        ins.action_validate_result()
        self.assertAlmostEqual(self._qty(lot), 70.0)
        if self.has_epc:
            self.assertEqual(lot.x_epc, epc_v4(lot.name))

    def test_split_new_piece(self):
        """R-08, board 8: 97,20 -> 60,00 + 37,20; the new piece gets a new ID, EPC and the roll data."""
        lot = self._roll(97.20, mst_lot_warna="02", mst_pcs_vendor="0007", mst_k3l="K3L:21")
        ins = self._inspection(lot, 0.0, piece_ids=[(0, 0, {"qty": 60.0, "lot_warna": "19"}),
                                                    (0, 0, {"qty": 37.20, "lot_warna": "20"})])
        ins.action_validate_result()
        self.assertEqual((ins.result_type_done, ins.conversion_ids.conversion_type), ("split", "split"))
        piece = ins.piece_ids.new_lot_id
        self.assertTrue(piece.mst_is_roll_id)
        self.assertEqual(piece.mst_origin_lot_id, lot)
        self.assertEqual((piece.mst_pcs_vendor, piece.mst_k3l, piece.mst_lot_warna), ("0007", "K3L:21", "20"))
        self.assertEqual((self._qty(lot), lot.mst_lot_warna), (60.0, "19"))
        if self.has_epc:
            self.assertEqual(piece.x_epc, epc_v4(piece.name))

    def test_split_with_product_change_refused(self):
        """Board 9: to change part of a roll, split first."""
        lot = self._roll(90.0)
        ins = self._inspection(lot, 0.0, product_result_id=self.product_a2.id, reason="x",
                               piece_ids=[(0, 0, {"qty": 50.0}), (0, 0, {"qty": 40.0})])
        with self.assertRaises(UserError):
            ins.action_validate_result()

    def test_validate_rights(self):
        """R-05: only QC Koordinator and Group Leader validate."""
        user = self.env["res.users"].create({
            "name": "Inspector", "login": "qc_inspector_test",
            "groups_id": [(6, 0, [self.env.ref("stock.group_stock_user").id,
                                  self.env.ref("mst17_qc_roll.group_qc_inspector").id])]})
        ins = self._inspection(self._roll(10.0), 10.0)
        with self.assertRaises(AccessError):
            ins.with_user(user).action_validate_result()
        user.groups_id |= self.env.ref("mst17_qc_roll.group_qc_group_leader")
        self.assertTrue(user.has_group("mst17_qc_roll.group_qc_koordinator"))

    def test_receipt_roll_id_and_history(self):
        """R-01/R-02: after the start date a receipt lot is an ID Roll; receiving and sending to QC are logged."""
        self.env.company.mst_roll_id_start = "2020-01-01"
        receipt = self.env["stock.picking"].create({
            "picking_type_id": self.wh.in_type_id.id, "partner_id": self.env.user.partner_id.id,
            "location_id": self.env.ref("stock.stock_location_suppliers").id,
            "location_dest_id": self.wh.lot_stock_id.id,
            "move_ids": [(0, 0, {"name": "roll", "product_id": self.product.id, "product_uom_qty": 59.60,
                                 "product_uom": self.product.uom_id.id,
                                 "location_id": self.env.ref("stock.stock_location_suppliers").id,
                                 "location_dest_id": self.wh.lot_stock_id.id})]})
        receipt.action_confirm()
        receipt.move_ids.quantity = 59.60
        self.assertTrue(ROLL_ID.match(receipt.move_line_ids.lot_name or ""))
        receipt.move_ids.picked = True
        receipt.button_validate()
        lot = receipt.move_line_ids.lot_id
        self.assertTrue(lot.mst_is_roll_id)
        self.assertEqual(self._events(lot), ["masuk"])

        move = self.env["stock.move"].create({
            "name": "to QC", "product_id": self.product.id, "product_uom_qty": 59.60,
            "product_uom": self.product.uom_id.id,
            "location_id": self.wh.lot_stock_id.id, "location_dest_id": self.qc.id})
        move._action_confirm()
        move._action_assign()
        move.picked = True
        move._action_done()
        self.assertEqual(sorted(self._events(lot)), ["kirim_qc", "masuk"])

    def test_preview(self):
        """Board 7, last column: what Odoo will make."""
        lot = self._roll(108.54, mst_lot_warna="01")
        ins = self._inspection(lot, 105.0, lot_warna_result="19")
        self.assertEqual(ins.result_type, "mixed")
        self.assertEqual(ins.result_preview, "Lot Warna + Qty Correction MO, same lot")
