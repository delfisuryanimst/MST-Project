from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError


class QcAlertExecuteWizard(models.TransientModel):
    """Bulk Execute: make a draft Scrap and/or a draft Simplified MO for many Quality Alerts at once.

    Nothing moves stock here. The user checks the drafts afterwards (and edits a Simplified MO that
    has to make several products), then validates them.
    """

    _name = "qc.alert.execute.wizard"
    _description = "Execute Quality Alerts"

    # All alerts run in one request; a small batch never comes near the server time limit.
    MAX_ALERTS = 50

    line_ids = fields.One2many("qc.alert.execute.wizard.line", "wizard_id", string="Alerts")
    note = fields.Char(readonly=True)
    team_id = fields.Many2one(
        "qc.team", default=lambda self: self.env.user._get_qc_team(),
        help="Set on every alert that gets a draft. Clear it to keep the team of each alert.")
    user_id = fields.Many2one(
        "res.users", string="Responsible", default=lambda self: self.env.user,
        help="Set on every alert that gets a draft. Clear it to keep the responsible of each alert.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get("active_model") != "qc.alert":
            return values
        selected = self.env["qc.alert"].browse(self.env.context.get("active_ids", []))
        # An alert that already has a Scrap and a Simplified MO (draft or done) has nothing left to do.
        alerts = selected.filtered(lambda alert: not alert.scrap_ids or not alert._active_conversion())
        if not alerts:
            raise UserError(_(
                "Nothing left to execute: each of the %s selected Quality Alerts already has a Scrap and "
                "a Simplified MO (draft or done).", len(selected)))
        self._check_max_alerts(len(alerts))
        if len(alerts) < len(selected):
            values["note"] = _(
                "%(shown)s of %(total)s alerts are listed. The other %(left)s already have a Scrap and a "
                "Simplified MO.", shown=len(alerts), total=len(selected), left=len(selected) - len(alerts))
        values["line_ids"] = [Command.create({
            "alert_id": alert.id,
            "scrap_qty": 0.0 if alert.scrap_ids else max(alert._scrap_remaining(), 0.0),
            "downgrade_qty": 0.0 if alert._active_conversion() else alert._downgrade_remaining(),
        }) for alert in alerts]
        return values

    @api.model
    def _check_max_alerts(self, count):
        if count > self.MAX_ALERTS:
            raise UserError(_(
                "%(count)s alerts are listed. Execute at most %(max)s alerts at a time.",
                count=count, max=self.MAX_ALERTS))

    def action_create_drafts(self):
        self.ensure_one()
        self._check_max_alerts(len(self.line_ids))
        if any(line.scrap_qty < 0 or line.downgrade_qty < 0 for line in self.line_ids):
            raise UserError(_("Quantities cannot be negative."))
        assignment = {key: record.id for key, record in (("team_id", self.team_id), ("user_id", self.user_id))
                      if record}
        skipped = []
        counts = {"scraps": 0, "conversions": 0, "untouched": 0}
        for line in self.line_ids:
            alert = line.alert_id
            do_scrap, do_downgrade = line.scrap_qty > 0, line.downgrade_qty > 0
            if not (do_scrap or do_downgrade):
                counts["untouched"] += 1
                continue
            reason = line._get_skip_reason()
            if reason:
                skipped.append((alert.name, reason))
                continue
            try:
                with self.env.cr.savepoint():
                    if do_scrap:
                        alert._create_draft_scrap(line.scrap_qty)
                    if do_downgrade:
                        alert._create_draft_conversion(line.downgrade_qty, line.result_product_id)
                    if assignment:
                        alert.write(assignment)
            except UserError as error:
                skipped.append((alert.name, error.args[0]))
                continue
            counts["scraps"] += do_scrap
            counts["conversions"] += do_downgrade
        message = _(
            "Drafts created: %(scraps)s Scrap(s) and %(conversions)s Simplified MO(s). "
            "Check them and validate them to move the stock.", **counts)
        if counts["untouched"]:
            message += _(" %s alert(s) without a quantity were left as they are.", counts["untouched"])
        return self.env["qc.alert"]._bulk_result_action(_("Execute Alerts"), message, skipped)


class QcAlertExecuteWizardLine(models.TransientModel):
    _name = "qc.alert.execute.wizard.line"
    _description = "Execute Quality Alert Line"

    wizard_id = fields.Many2one("qc.alert.execute.wizard", required=True, ondelete="cascade")
    alert_id = fields.Many2one("qc.alert", required=True, readonly=True)
    product_id = fields.Many2one(related="alert_id.product_id")
    lot_id = fields.Many2one(related="alert_id.lot_id")
    scrap_proposed_qty = fields.Float(related="alert_id.scrap_proposed_qty")
    scrap_qty = fields.Float(digits=(16, 2))
    downgrade_proposed_qty = fields.Float(related="alert_id.downgrade_proposed_qty")
    downgrade_qty = fields.Float(digits=(16, 2))
    result_product_id = fields.Many2one(
        "product.product", string="Result Product", domain=[("type", "=", "product")],
        help="Only for the downgrade: the product the Simplified MO makes, with the Downgrade Qty. "
             "A Scrap makes no product. To make several products, edit the draft Simplified MO afterwards.")
    has_scrap = fields.Boolean(compute="_compute_documents")
    has_conversion = fields.Boolean(compute="_compute_documents")
    note = fields.Char(compute="_compute_documents")

    @api.depends("alert_id.scrap_ids.state", "alert_id.conversion_ids.state")
    def _compute_documents(self):
        for line in self:
            scrap = line.alert_id.scrap_ids[:1]
            conversion = line.alert_id._active_conversion() if line.alert_id else self.env["qc.conversion"]
            line.has_scrap = bool(scrap)
            line.has_conversion = bool(conversion)
            notes = []
            if scrap:
                notes.append(_("Scrap made (%s)", line._state_label(scrap)))
            if conversion:
                notes.append(_("%(smo)s made (%(state)s)", smo=conversion.reference, state=line._state_label(conversion)))
            line.note = ", ".join(notes)

    @staticmethod
    def _state_label(record):
        return dict(record._fields["state"]._description_selection(record.env)).get(record.state, record.state)

    def _get_skip_reason(self):
        """Why this line cannot get its drafts; False when it can."""
        self.ensure_one()
        alert = self.alert_id
        do_scrap, do_downgrade = self.scrap_qty > 0, self.downgrade_qty > 0
        if not alert.product_id:
            return _("no product")
        if do_scrap and self.has_scrap:
            return _("already has a Scrap")
        if do_downgrade and self.has_conversion:
            return _("already has a Simplified MO")
        if do_downgrade and not self.result_product_id:
            return _("no Result Product")
        if do_downgrade and self.result_product_id == alert.product_id:
            return _("the Result Product is the alert's own product")
        return False
