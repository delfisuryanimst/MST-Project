from odoo import api, fields, models

from .stock_picking import QC_STATUS

QC_STATUS_LABELS = dict(QC_STATUS)


class QcDashboard(models.Model):
    _name = "qc.dashboard"
    _description = "Quality Control Dashboard"
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    team_id = fields.Many2one("qc.team", string="QC Team", ondelete="cascade")
    picking_type_ids = fields.Many2many(
        "stock.picking.type", string="Operation Types",
        help="Leave empty to count all operation types.",
    )
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    check_count = fields.Integer(compute="_compute_counts")
    alert_count = fields.Integer(compute="_compute_counts")
    pending_count = fields.Integer(compute="_compute_counts")
    failed_count = fields.Integer(compute="_compute_counts")
    partial_count = fields.Integer(compute="_compute_counts")
    passed_count = fields.Integer(compute="_compute_counts")

    def _compute_counts(self):
        Check = self.env["qc.check"]
        Alert = self.env["qc.alert"]
        Picking = self.env["stock.picking"]
        for card in self:
            card.check_count = Check.search_count(card._check_domain())
            card.alert_count = Alert.search_count(card._alert_domain())
            card.pending_count = Picking.search_count(card._picking_domain("pending"))
            card.failed_count = Picking.search_count(card._picking_domain("failed"))
            card.partial_count = Picking.search_count(card._picking_domain("partial"))
            card.passed_count = Picking.search_count(card._picking_domain("passed"))

    @api.model
    def _create_team_cards(self, teams):
        """One card per team, copied from the team-less ("Undefined") cards."""
        for team in teams:
            for template in self.sudo().search([("team_id", "=", False)]):
                if self.sudo().search_count([("team_id", "=", team.id), ("name", "=", template.name)]):
                    continue
                template.copy({"team_id": team.id})

    def _check_domain(self):
        self.ensure_one()
        domain = [("team_id", "=", self.team_id.id)]
        if self.picking_type_ids:
            domain.append(("picking_type_id", "in", self.picking_type_ids.ids))
        return domain

    def _alert_domain(self):
        self.ensure_one()
        domain = [("team_id", "=", self.team_id.id)]
        if self.picking_type_ids:
            domain.append(("picking_id.picking_type_id", "in", self.picking_type_ids.ids))
        return domain

    def _picking_domain(self, status=None):
        self.ensure_one()
        domain = [("has_qc", "=", True), ("qc_check_ids.team_id", "=", self.team_id.id)]
        if self.picking_type_ids:
            domain.append(("picking_type_id", "in", self.picking_type_ids.ids))
        if status:
            domain.append(("qc_status", "=", status))
        return domain

    def _action_open(self, xmlid, domain):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action["domain"] = domain
        action["context"] = {"default_team_id": self.team_id.id}
        return action

    def action_open_checks(self):
        return self._action_open("quality_control.qc_check_action", self._check_domain())

    def action_open_alerts(self):
        return self._action_open("quality_control.qc_alert_action", self._alert_domain())

    def action_open_pickings(self):
        self.ensure_one()
        status = self.env.context.get("qc_status")
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.action_qc_picking_all")
        action["domain"] = self._picking_domain(status)
        action["display_name"] = "%s - %s" % (self.name, QC_STATUS_LABELS.get(status, "QC"))
        return action
