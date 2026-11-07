from odoo import api, fields, models


class MstQcPiece(models.Model):
    """One piece of a roll that QC cuts (TSD: piece_ids). The longest piece keeps the roll ID."""

    _name = "mst.qc.piece"
    _description = "Roll Piece"
    _order = "inspection_id, sequence, id"

    inspection_id = fields.Many2one("mst.qc.inspection", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer(default=10)
    qty = fields.Float(digits=(16, 2), required=True)
    lot_warna = fields.Char(string="Lot Warna", help="Empty keeps the Lot Warna of the roll.")
    keeps_id = fields.Boolean(
        string="Keeps Roll ID", compute="_compute_keeps_id",
        help="The longest piece keeps the roll ID and its tag; the other pieces get a new ID Roll.")
    new_lot_id = fields.Many2one("stock.lot", string="New ID Roll", readonly=True)

    @api.depends("inspection_id.piece_ids.qty", "inspection_id.piece_ids.sequence")
    def _compute_keeps_id(self):
        for piece in self:
            piece.keeps_id = piece == piece.inspection_id.piece_ids._get_keeper()

    def _get_keeper(self):
        """The longest piece (the first one when two have the same length)."""
        return self.sorted(lambda p: (-p.qty, p.sequence, p.id or 0))[:1]
