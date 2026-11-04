from odoo import _, api, fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    all_picking_ids = fields.Many2many(
        comodel_name="stock.picking",
        string="All Transfers",
        compute="_compute_all_picking_ids",
        help="Receipts and their follow-up transfers (2/3-step routes).",
    )
    all_picking_count = fields.Integer(compute="_compute_all_picking_ids")

    @api.depends("picking_ids", "order_line.move_ids.move_dest_ids.picking_id")
    def _compute_all_picking_ids(self):
        for order in self:
            pickings = order._get_chained_moves().picking_id
            order.all_picking_ids = pickings
            order.all_picking_count = len(pickings)

    def _get_chained_moves(self):
        """PO moves plus their downstream moves (Receipt -> QC -> Stock)."""
        self.ensure_one()
        moves = self.order_line.move_ids
        next_moves = moves.move_dest_ids - moves
        while next_moves:
            moves |= next_moves
            next_moves = next_moves.move_dest_ids - moves
        return moves

    def action_view_all_pickings(self):
        action = self._get_action_view_picking(self.all_picking_ids)
        action["name"] = _("Transfers")
        return action
