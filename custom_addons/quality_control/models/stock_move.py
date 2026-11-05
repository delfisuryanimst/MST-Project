from odoo import fields, models


class StockMove(models.Model):
    _inherit = "stock.move"

    qc_conversion_id = fields.Many2one("qc.conversion", string="Stock Conversion", index=True, copy=False)

    qty_a = fields.Float(digits=(16, 2))
    qty_a_star = fields.Float(string="Qty A*", digits=(16, 2))
    qty_b = fields.Float(digits=(16, 2))
    qty_ao_a2 = fields.Float(string="Qty AO / A2", digits=(16, 2))
    qty_a_grey_c_a1 = fields.Float(string="A Grey C / A1", digits=(16, 2))
    qty_bs = fields.Float(string="Qty BS", digits=(16, 2))
    k3l = fields.Char()

    def _action_confirm(self, merge=True, merge_into=False):
        # Hooked on moves so follow-up transfers of 2/3-step routes are covered too.
        moves = super()._action_confirm(merge=merge, merge_into=merge_into)
        for picking in moves.picking_id:
            picking._create_qc_checks(moves.filtered(lambda m: m.picking_id == picking))
        return moves
