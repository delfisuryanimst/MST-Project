from odoo import api, models

# Check fields that change the default result of the roll.
SYNC_FIELDS = {"quality_state", "qty_to_scrap", "qty_to_downgrade", "lot_id", "lot_name"}


class QcCheck(models.Model):
    """When QC finishes a Quality Check (Pass or Fail), its roll gets a Roll Inspection line by itself.

    The old Quality Control flow is not changed: this only reacts to it (FSD "Odoo membuat satu baris
    QC per roll"). Only rolls that were requested for QC have a check, so only they get a line.
    """

    _inherit = "qc.check"

    @api.model_create_multi
    def create(self, vals_list):
        checks = super().create(vals_list)
        checks._mst_sync_roll_inspection()
        return checks

    def write(self, vals):
        result = super().write(vals)
        if SYNC_FIELDS & set(vals):
            self._mst_sync_roll_inspection()
        return result

    def _mst_get_lot(self):
        self.ensure_one()
        if self.lot_id:
            return self.lot_id
        if not self.lot_name:
            return self.env["stock.lot"]
        return self.env["stock.lot"].sudo().search([
            ("name", "=", self.lot_name), ("product_id", "=", self.product_id.id),
        ], limit=1)

    def _mst_sync_roll_inspection(self):
        """Create, update or remove the draft Roll Inspection of each check.

        Pass / Fail: make the line, or update it while it is still draft.
        Back to To Do: remove the draft line (it is made again at the next Pass / Fail).
        A validated line is never touched.
        """
        Inspection = self.env["mst.qc.inspection"].sudo()
        for check in self.sudo():
            lines = Inspection.search([("check_id", "=", check.id)])
            drafts = lines.filtered(lambda line: line.result_state == "draft")
            if check.quality_state not in ("pass", "fail"):
                drafts.unlink()
                continue
            if lines - drafts:
                continue  # already validated
            lot = check._mst_get_lot()
            if not lot or not check.picking_id:
                continue
            if drafts:
                for draft in drafts:
                    draft.write(Inspection._vals_from_check(check, draft.qty_roll))
                continue
            if Inspection.search_count([("lot_id", "=", lot.id), ("result_state", "=", "draft")]):
                continue  # the roll already waits in another line
            location = Inspection._current_location(lot)
            if not location:
                continue  # the roll is not in stock anymore
            vals = {"picking_id": check.picking_id.id, "lot_id": lot.id, "location_id": location.id}
            vals.update(Inspection._vals_from_check(check, Inspection._lot_qty(lot, location)))
            Inspection.create(vals)
