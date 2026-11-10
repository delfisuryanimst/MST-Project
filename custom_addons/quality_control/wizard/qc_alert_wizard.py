from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class QcAlertWizard(models.TransientModel):
    _name = "qc.alert.wizard"
    _description = "Create Quality Alerts"

    # Creating is light (about 15 ms per alert), but a long line list makes the dialog slow in the browser.
    MAX_ALERTS =100

    picking_id = fields.Many2one("stock.picking", string="Transfer", readonly=True)
    title = fields.Char(help="Used for every line whose Title is empty. Leave it empty for an automatic title, "
                             "like DOWNGRADE, SCRAP DAN RUBAH QTY.")
    team_id = fields.Many2one("qc.team", help="Used for every line whose Team is empty.")
    user_id = fields.Many2one(
        "res.users", string="Responsible", default=lambda self: self.env.user,
        help="Used for every line whose Responsible is empty. Clear it to keep the user of each check.")
    line_ids = fields.One2many("qc.alert.wizard.line", "wizard_id", string="Failed Quality Checks")
    skipped_note = fields.Char(readonly=True)

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get("active_model") == "qc.check":
            selected = self.env["qc.check"].browse(self.env.context.get("active_ids", []))
        else:
            selected = self.env["stock.picking"].browse(values.get("picking_id")).qc_check_ids
        self._check_max_alerts(len(selected))
        failed = selected.filtered(lambda c: c.quality_state == "fail")
        checks = failed.filtered(lambda c: not c.alert_ids)
        counts = {
            "total": len(selected),
            "not_failed": len(selected) - len(failed),
            "with_alert": len(failed) - len(checks),
        }
        if not checks:
            raise UserError(_(
                "None of the %(total)s Quality Checks can get a Quality Alert: "
                "%(not_failed)s not failed, %(with_alert)s with an existing Quality Alert.", **counts))
        if len(checks) < len(selected):
            values["skipped_note"] = _(
                "%(shown)s of %(total)s Quality Checks are listed. Left out: "
                "%(not_failed)s not failed, %(with_alert)s with an existing Quality Alert.",
                shown=len(checks), **counts)
        values.setdefault("team_id", (checks.team_id[:1] or self.env.user._get_qc_team()).id)
        values.setdefault("line_ids", [Command.create({
            "check_id": check.id,
            "team_id": values["team_id"],
            "user_id": values.get("user_id"),
            "scrap_proposed_qty": check.qty_to_scrap,
            "downgrade_proposed_qty": check.qty_to_downgrade,
        }) for check in checks])
        return values

    @api.model
    def _check_max_alerts(self, count):
        if count > self.MAX_ALERTS:
            raise UserError(_(
                "You selected %(count)s Quality Checks. Create at most %(max)s Quality Alerts at a time.",
                count=count, max=self.MAX_ALERTS))

    @api.onchange("title", "team_id", "user_id")
    def _onchange_header(self):
        """Apply the values on top to every line; an empty Team or Responsible falls back to the check's own."""
        for line in self.line_ids:
            line.title = self.title
            line.team_id = self.team_id or line.check_id.team_id
            line.user_id = self.user_id or line.check_id.user_id

    def action_create_alerts(self):
        self.ensure_one()
        self._check_max_alerts(len(self.line_ids))
        if not self.line_ids:
            raise UserError(_("Select at least one product to create a Quality Alert for."))
        vals_list = []
        for line in self.line_ids:
            vals = line.check_id._prepare_alert_vals()
            # A line without a Title (and no Title on top) gets one from its proposed actions.
            auto_title = self.env["qc.alert"]._get_auto_title(
                line.downgrade_proposed_qty, line.scrap_proposed_qty, vals.get("qty_adjust_proposed_qty", 0.0))
            vals.update({
                "title": line.title or self.title or auto_title,
                "team_id": (line.team_id or self.team_id).id or vals["team_id"],
                "user_id": (line.user_id or self.user_id).id or vals["user_id"],
                "scrap_proposed_qty": line.scrap_proposed_qty,
                "downgrade_proposed_qty": line.downgrade_proposed_qty,
            })
            vals_list.append(vals)
        alerts = self.env["qc.alert"].create(vals_list)
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_alert_action")
        action.update({"domain": [("id", "in", alerts.ids)], "context": {}})
        if len(alerts) == 1:
            action.update({"views": [(False, "form")], "res_id": alerts.id})
        return action


class QcAlertWizardLine(models.TransientModel):
    _name = "qc.alert.wizard.line"
    _description = "Create Quality Alert Line"

    wizard_id = fields.Many2one("qc.alert.wizard", required=True, ondelete="cascade")
    check_id = fields.Many2one("qc.check", string="Quality Check", required=True, readonly=True)
    product_id = fields.Many2one(related="check_id.product_id")
    lot_name = fields.Char(related="check_id.lot_name")
    fail_qty = fields.Float(related="check_id.fail_qty")
    pass_qty = fields.Float(related="check_id.pass_qty")
    title = fields.Char(help="Leave empty to use the Title on top, or an automatic title from the proposed actions.")
    team_id = fields.Many2one("qc.team", help="Leave empty to use the Team on top.")
    user_id = fields.Many2one("res.users", string="Responsible", help="Leave empty to use the Responsible on top.")
    scrap_proposed_qty = fields.Float(string="Scrap (Proposed)", digits=(16, 2))
    downgrade_proposed_qty = fields.Float(string="Downgrade (Proposed)", digits=(16, 2))
