from odoo import api, fields, models


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    @api.model_create_multi
    def create(self, vals_list):
        lines = super().create(vals_list)
        lines._mst_set_roll_id()
        return lines

    def _mst_set_roll_id(self):
        """From the company's ID Roll start date, a receipt line without a lot name gets the next ID Roll."""
        today = fields.Date.context_today(self)
        for line in self:
            start = line.company_id.mst_roll_id_start
            if (start and start <= today and line.picking_code == "incoming" and line.product_id.tracking == "lot"
                    and line.picking_type_id.use_create_lots and not line.lot_id and not line.lot_name):
                line.lot_name = self.env["ir.sequence"].next_by_code("mst.roll.id")

    def _create_and_assign_production_lot(self):
        super()._create_and_assign_production_lot()
        for line in self:
            order = line.move_id.purchase_line_id.order_id
            if order and line.lot_id.mst_is_roll_id and not line.lot_id.mst_po_id:
                line.lot_id.mst_po_id = order


class StockMove(models.Model):
    _inherit = "stock.move"

    def _action_done(self, cancel_backorder=False):
        moves = super()._action_done(cancel_backorder=cancel_backorder)
        moves._mst_log_roll_moves()
        return moves

    def _mst_roll_event(self):
        self.ensure_one()
        source, dest = self.location_id, self.location_dest_id
        if dest.usage == "customer":
            return "kirim_customer"
        if source.usage == "supplier":
            return "masuk"
        qc_location = self.company_id.mst_qc_location_id
        if qc_location and dest._child_of(qc_location) and not source._child_of(qc_location):
            return "kirim_qc"
        if source.usage == "internal" and dest.usage == "internal":
            return "pindah"
        return False

    def _mst_log_roll_moves(self):
        """Roll history of done transfers. QC documents (Simplified MO, Scrap) log their own lines."""
        vals_list = []
        for move in self.filtered(lambda m: m.state == "done"):
            lines = move.move_line_ids.filtered(lambda line: line.lot_id.mst_is_roll_id)
            event = lines and move._mst_roll_event()
            if not event:
                continue
            document = move.picking_id or move
            vals_list += [{
                "lot_id": line.lot_id.id,
                "event": event,
                "old_value": line.location_id.display_name,
                "new_value": line.location_dest_id.display_name,
                "res_model": document._name,
                "res_id": document.id,
                "note": "%s %s" % (line.quantity, line.product_uom_id.name),
            } for line in lines]
        if vals_list:
            self.env["mst.roll.history"].sudo().create(vals_list)
