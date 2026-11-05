from odoo import _, api, fields, models
from odoo.exceptions import UserError


class QcAlertWizard(models.TransientModel):
    _name = "qc.alert.wizard"
    _description = "Create Quality Alerts from a Transfer"

    picking_id = fields.Many2one("stock.picking", string="Transfer", required=True, readonly=True)
    check_ids = fields.Many2many(
        "qc.check", string="Failed Quality Checks", required=True,
        domain="[('picking_id', '=', picking_id), ('quality_state', '=', 'fail')]",
        help="Produk (QC Failed) yang akan dibuatkan Quality Alert.",
    )
    title = fields.Char(
        string="Title", required=True, help="Judul untuk semua alert yang dibuat, mis. SCRAP atau DOWNGRADE.")
    team_id = fields.Many2one(
        "qc.team", string="Team", help="Tim yang ditugaskan menindaklanjuti kasus ini.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        picking = self.env["stock.picking"].browse(values.get("picking_id"))
        if picking:
            failed = picking.qc_check_ids.filtered(lambda c: c.quality_state == "fail" and not c.alert_ids)
            values.setdefault("check_ids", [fields.Command.set(failed.ids)])
            values.setdefault("team_id", failed.team_id[:1].id)
        return values

    def action_create_alerts(self):
        self.ensure_one()
        if not self.check_ids:
            raise UserError(_("Pilih minimal satu produk yang akan dibuatkan Quality Alert."))
        vals_list = []
        for check in self.check_ids:
            vals = check._prepare_alert_vals()
            vals["title"] = self.title
            if self.team_id:
                vals["team_id"] = self.team_id.id
            vals_list.append(vals)
        alerts = self.env["qc.alert"].create(vals_list)
        return self.picking_id._action_open_alerts(alerts)
