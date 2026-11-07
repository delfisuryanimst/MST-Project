from odoo import _, fields, models
from odoo.exceptions import UserError


class MstQcLoadRolls(models.TransientModel):
    """Make one Roll Inspection line per roll of the chosen QCP transfers."""

    _name = "mst.qc.load.rolls"
    _description = "Load Rolls from QCP"

    picking_ids = fields.Many2many(
        "stock.picking", string="QCP Transfers", required=True,
        domain=[("state", "=", "done"), ("move_line_ids.lot_id", "!=", False)],
        help="Done transfers that brought the rolls into the QC location.")

    def action_load(self):
        self.ensure_one()
        Inspection = self.env["mst.qc.inspection"]
        created = Inspection
        for picking in self.picking_ids:
            location = picking.location_dest_id
            known = Inspection.search([("picking_id", "=", picking.id)]).lot_id
            for lot in picking.move_line_ids.lot_id - known:
                quants = self.env["stock.quant"].sudo().search([
                    ("lot_id", "=", lot.id), ("location_id", "child_of", location.id), ("quantity", ">", 0)])
                if not quants:
                    continue  # the roll already left the QC location
                vals = {"picking_id": picking.id, "lot_id": lot.id, "location_id": location.id}
                vals.update(Inspection._vals_from_check(
                    Inspection._find_check(picking, lot), sum(quants.mapped("quantity"))))
                created |= Inspection.create(vals)
        if not created:
            raise UserError(_(
                "No new roll to load: every roll of these transfers is loaded already or is no longer in "
                "the QC location."))
        action = self.env["ir.actions.act_window"]._for_xml_id("mst17_qc_roll.action_mst_qc_inspection")
        action["domain"] = [("picking_id", "in", self.picking_ids.ids)]
        action["context"] = {"search_default_draft": 1}
        return action
