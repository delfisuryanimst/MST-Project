from odoo import api, fields, models


class QcTeam(models.Model):
    _name = "qc.team"
    _description = "Quality Control Team"
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True, translate=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    color = fields.Integer()
    user_ids = fields.Many2many("res.users", string="Users")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    check_ids = fields.One2many("qc.check", "team_id", string="Quality Checks")
    alert_ids = fields.One2many("qc.alert", "team_id", string="Quality Alerts")
    check_todo_count = fields.Integer(compute="_compute_counts")
    check_fail_count = fields.Integer(compute="_compute_counts")
    check_pass_count = fields.Integer(compute="_compute_counts")
    alert_open_count = fields.Integer(compute="_compute_counts")

    @api.depends("check_ids.quality_state", "alert_ids.stage_id")
    def _compute_counts(self):
        for team in self:
            checks = team.check_ids
            team.check_todo_count = len(checks.filtered(lambda c: c.quality_state == "todo"))
            team.check_fail_count = len(checks.filtered(lambda c: c.quality_state == "fail"))
            team.check_pass_count = len(checks.filtered(lambda c: c.quality_state == "pass"))
            team.alert_open_count = len(team.alert_ids.filtered(lambda a: not a.stage_id.done))

    @api.model_create_multi
    def create(self, vals_list):
        teams = super().create(vals_list)
        self.env["qc.dashboard"]._create_team_cards(teams)
        return teams

    @api.model
    def _get_team_for_user(self, user):
        return self.search([("user_ids", "in", user.id)], limit=1)

    def _action_open(self, xmlid, domain):
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action["domain"] = [("team_id", "=", self.id)] + domain
        action["context"] = {"default_team_id": self.id}
        return action

    def action_open_checks(self):
        return self._action_open("quality_control.qc_check_action", [])

    def action_open_checks_todo(self):
        return self._action_open("quality_control.qc_check_action", [("quality_state", "=", "todo")])

    def action_open_checks_fail(self):
        return self._action_open("quality_control.qc_check_action", [("quality_state", "=", "fail")])

    def action_open_alerts(self):
        return self._action_open("quality_control.qc_alert_action", [])
