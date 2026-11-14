import re

from odoo import api, fields, models

ROLL_ID = re.compile(r"^R(\d{2})-(\d{6})$")
# Roll data that a piece, a B Grade lot or a product change lot takes over from its roll.
ROLL_DATA = ("mst_lot_warna", "mst_pcs_vendor", "mst_po_id", "mst_k3l", "mst_asi_name")


def epc_v4(name):
    """EPC v4 of an ID Roll: "4" + zeros + year (2 digits) + number (6 digits), 32 hex.
    R26-000123 -> 40000000000000000000000026000123."""
    match = ROLL_ID.match(name or "")
    return match and "4" + "0" * 23 + match.group(1) + match.group(2)


class StockLot(models.Model):
    _inherit = "stock.lot"

    mst_is_roll_id = fields.Boolean(string="ID Roll", readonly=True, copy=False,
                                    help="The lot name is an ID Roll (R26-000123) that never changes.")
    mst_lot_warna = fields.Char(string="Lot Warna", tracking=True, copy=False)
    mst_pcs_vendor = fields.Char(string="No. Pcs", copy=False, help="Piece number of the vendor.")
    mst_po_id = fields.Many2one("purchase.order", string="PO", copy=False)
    mst_k3l = fields.Char(string="K3L", copy=False)
    mst_asi_name = fields.Char(string="Nama ASI (Denim)", copy=False, help="Lot name given by ASI (old QR).")
    mst_item_a_id = fields.Many2one(
        "product.product", string="Item A", tracking=True, copy=False,
        help="For a B Grade lot: the Grade A product it came from.")
    mst_origin_lot_id = fields.Many2one(
        "stock.lot", string="Lot Asal", copy=False, readonly=True,
        help="For a piece cut from another roll: the roll it was cut from.")
    mst_history_ids = fields.One2many("mst.roll.history", "lot_id", string="Roll History")

    @api.model_create_multi
    def create(self, vals_list):
        has_epc = "x_epc" in self._fields
        for vals in vals_list:
            if ROLL_ID.match(vals.get("name") or ""):
                vals.setdefault("mst_is_roll_id", True)
                if has_epc:
                    vals.setdefault("x_epc", epc_v4(vals["name"]))
        return super().create(vals_list)

    def _mst_roll_data(self):
        self.ensure_one()
        return {name: self[name].id if name == "mst_po_id" else self[name] for name in ROLL_DATA}

    def _mst_move_epc(self, target, document):
        """Give the RFID tag to the lot that holds the roll now (same name, other product).
        Only when this lot is empty, so a scan never finds a lot without stock."""
        self.ensure_one()
        if "x_epc" not in self._fields or not self.x_epc or target.x_epc:
            return
        self.invalidate_recordset(["product_qty"])
        if self.product_qty > 0:
            return
        epc = self.x_epc
        self.sudo().x_epc = False
        target.sudo().x_epc = epc
        self._roll_event("epc", epc, False, document, target.product_id.display_name)
        target._roll_event("epc", False, epc, document, self.product_id.display_name)

    def _roll_event(self, event, old_value=False, new_value=False, document=None, note=False):
        """Add one line to the roll history. Only the module code writes it (users cannot)."""
        self.env["mst.roll.history"].sudo().create([{
            "lot_id": lot.id,
            "event": event,
            "old_value": "" if old_value in (False, None) else str(old_value),
            "new_value": "" if new_value in (False, None) else str(new_value),
            "res_model": document._name if document else False,
            "res_id": document.id if document else False,
            "note": note or False,
        } for lot in self])
