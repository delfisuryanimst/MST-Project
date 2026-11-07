from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    mst_lot_warna = fields.Char(string="Lot Warna", tracking=True, copy=False)
    mst_item_a_id = fields.Many2one(
        "product.product", string="Item A", tracking=True, copy=False,
        help="For a B Grade lot: the Grade A product it came from.")
    mst_origin_lot_id = fields.Many2one(
        "stock.lot", string="Lot Asal", copy=False, readonly=True,
        help="For a piece cut from another roll: the roll it was cut from.")
    mst_history_ids = fields.One2many("mst.roll.history", "lot_id", string="Roll History")

    def _roll_event(self, event, old_value=False, new_value=False, document=None, note=False):
        """Add one line to the roll history. Only the module code writes it (users cannot)."""
        History = self.env["mst.roll.history"].sudo()
        for lot in self:
            History.create({
                "lot_id": lot.id,
                "event": event,
                "old_value": "" if old_value in (False, None) else str(old_value),
                "new_value": "" if new_value in (False, None) else str(new_value),
                "res_model": document._name if document else False,
                "res_id": document.id if document else False,
                "note": note or False,
            })
