from datetime import date

from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .sales_delivery_engine import MONTHS_FULL, _wib_date

PERIODS = [("month", "Bulanan"), ("ytd_today", "YTD hari ini"), ("ytd_date", "YTD s/d tanggal")]


class SalesDeliveryReport(models.Model):
    _name = "sales.delivery.report"
    _description = "Data Pengiriman"
    _rec_name = "user_id"

    user_id = fields.Many2one("res.users", required=True, readonly=True, default=lambda self: self.env.user,
                              ondelete="cascade", index=True)
    period = fields.Selection(PERIODS, "Periode", default="month", required=True)
    month = fields.Selection("_selection_month", "Bulan", default=lambda self: self._current_month())
    until_date = fields.Date("Sampai", default=lambda self: _wib_date(fields.Datetime.now()))
    team_id = fields.Many2one("crm.team", "Tim", domain="[('id', 'in', allowed_team_ids)]")
    salesman_id = fields.Many2one("res.users", "Salesperson", domain="[('id', 'in', allowed_user_ids)]")
    measure = fields.Selection([("bruto", "Bruto"), ("netto", "Netto")], "Hitungan", default="bruto", required=True)
    allowed_team_ids = fields.Many2many("crm.team", compute="_compute_allowed")
    allowed_user_ids = fields.Many2many("res.users", compute="_compute_allowed")
    result_json = fields.Json("Hasil", readonly=True)

    _sql_constraints = [("user_unique", "unique(user_id)", "Setiap user hanya punya satu form Data Pengiriman.")]

    def _current_month(self):
        return _wib_date(fields.Datetime.now()).strftime("%Y-%m")

    def _selection_month(self):
        today = _wib_date(fields.Datetime.now())
        return [("%d-%02d" % (year, month), "%s %d" % (MONTHS_FULL[month - 1], year))
                for year in (today.year, today.year - 1) for month in range(12, 0, -1)
                if (year, month) <= (today.year, today.month)]

    def _compute_display_name(self):
        for report in self:
            report.display_name = _("Data Pengiriman")

    def _compute_allowed(self):
        scope = self.env["sales.delivery.engine"]._scope()
        Team = self.env["crm.team"].sudo()
        teams = Team.search([]) if scope["all"] else Team.browse(scope["team_ids"])
        if scope["all"]:
            users = self.env.ref("sales_team.group_sale_salesman").sudo().users
        else:
            users = teams.member_ids | teams.user_id | self.env.user
        for report in self:
            report.allowed_team_ids = teams
            report.allowed_user_ids = users

    @api.model
    def action_open_my(self):
        report = self.search([("user_id", "=", self.env.uid)], limit=1) or self.create({})
        report.action_show()
        return {
            "type": "ir.actions.act_window",
            "name": _("Data Pengiriman"),
            "res_model": self._name,
            "res_id": report.id,
            "views": [(False, "form")],
            "target": "current",
        }

    def _report(self, details=False):
        self.ensure_one()
        if self.period == "ytd_date" and not self.until_date:
            raise UserError(_("Isi tanggal sampai."))
        month = date(*map(int, (self.month or self._current_month()).split("-")), 1)
        return self.env["sales.delivery.engine"]._report(
            self.period, month, self.until_date, self.team_id.id, self.salesman_id.id, self.measure, details)

    def action_show(self):
        for report in self:
            report.result_json = report._report()

    def action_export(self):
        self.ensure_one()
        return {"type": "ir.actions.act_url", "url": "/mst17_sales_delivery/export/%s" % self.id, "target": "self"}

    def _export_xlsx(self):
        self.ensure_one()
        data = self._report(details=True)
        filters = [
            ("Periode", "%s · %s" % (dict(PERIODS)[self.period], data["label"])),
            ("Tim", self.team_id.name or "Semua tim saya"),
            ("Salesperson", self.salesman_id.name or "Semua"),
            ("Hitungan", dict(self._fields["measure"].selection)[self.measure]),
            ("Data per", data["stamp"]),
        ]
        content = self.env["sales.delivery.engine"]._xlsx(data, filters)
        return content, "Data Pengiriman %s %s.xlsx" % (data["label"].replace("/", "-"), self.measure.title())
