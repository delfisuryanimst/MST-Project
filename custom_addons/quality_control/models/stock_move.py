from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    qc_conversion_id = fields.Many2one("qc.conversion", string="Stock Conversion", index=True, copy=False)

    def _action_confirm(self, merge=True, merge_into=False):
        # Hooked on moves so follow-up transfers of 2/3-step routes are covered too.
        moves = super()._action_confirm(merge=merge, merge_into=merge_into)
        for picking in moves.picking_id:
            picking._create_qc_checks(moves.filtered(lambda m: m.picking_id == picking))
        return moves
