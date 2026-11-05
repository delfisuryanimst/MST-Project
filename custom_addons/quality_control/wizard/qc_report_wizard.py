from datetime import datetime, time, timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class QcReportWizardMixin(models.AbstractModel):
    _name = "qc.report.wizard.mixin"
    _description = "QC Report Wizard (common)"

    date_from = fields.Date(string="Start Date", required=True, default=lambda self: fields.Date.today().replace(day=1))
    date_to = fields.Date(string="End Date", required=True, default=fields.Date.context_today)
    product_ids = fields.Many2many("product.product", string="Product")

    @api.constrains("date_from", "date_to")
    def _check_dates(self):
        for wizard in self:
            if wizard.date_from > wizard.date_to:
                raise ValidationError(_("Start Date tidak boleh setelah End Date."))

    def _date_domain(self, field_name):
        """Inclusive date range on a datetime field (whole days)."""
        self.ensure_one()
        start = datetime.combine(self.date_from, time.min)
        end = datetime.combine(self.date_to + timedelta(days=1), time.min)
        return [(field_name, ">=", start), (field_name, "<", end)]

    def _print(self, report_xmlid, records):
        if not records:
            raise UserError(_("Tidak ada data untuk filter yang dipilih."))
        data = {"wizard_id": self.id, "record_ids": records.ids}
        return self.env.ref(report_xmlid).report_action(self, data=data)


class QcCheckReportWizard(models.TransientModel):
    _name = "qc.check.report.wizard"
    _inherit = "qc.report.wizard.mixin"
    _description = "Quality Check Report"

    group_by = fields.Selection(
        [("picking", "Picking Type"), ("product", "Product")], string="Group By",
        help="Kosongkan untuk satu tabel tanpa pengelompokan.")
    state = fields.Selection(
        [("pass", "Pass"), ("fail", "Fail"), ("both", "Both")], string="Status", default="both", required=True)

    def _get_records(self):
        self.ensure_one()
        domain = self._date_domain("control_date")
        domain.append(("quality_state", "in", ["pass", "fail"] if self.state == "both" else [self.state]))
        if self.product_ids:
            domain.append(("product_id", "in", self.product_ids.ids))
        return self.env["qc.check"].search(domain, order="control_date, id")

    def print_report(self):
        self.ensure_one()
        return self._print("quality_control.action_report_qc_check_summary", self._get_records())


class QcAlertReportWizard(models.TransientModel):
    _name = "qc.alert.report.wizard"
    _inherit = "qc.report.wizard.mixin"
    _description = "Quality Alert Report"

    stage_id = fields.Many2one("qc.alert.stage", string="Stage", required=True)
    user_ids = fields.Many2many("res.users", string="Responsible Users")

    def _get_records(self):
        self.ensure_one()
        domain = self._date_domain("create_date") + [("stage_id", "=", self.stage_id.id)]
        if self.product_ids:
            domain.append(("product_id", "in", self.product_ids.ids))
        if self.user_ids:
            domain.append(("user_id", "in", self.user_ids.ids))
        return self.env["qc.alert"].search(domain, order="create_date, id")

    def print_report(self):
        self.ensure_one()
        records = self._get_records()
        if not records and not self.user_ids:
            raise UserError(_("Tidak ada data untuk filter yang dipilih."))
        data = {"wizard_id": self.id, "record_ids": records.ids}
        return self.env.ref("quality_control.action_report_qc_alert_summary").report_action(self, data=data)


class ReportQcCheckSummary(models.AbstractModel):
    _name = "report.quality_control.report_qc_check_summary"
    _description = "Quality Check Summary Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env["qc.check.report.wizard"].browse(data.get("wizard_id"))
        checks = self.env["qc.check"].browse(data.get("record_ids", []))
        if not wizard.group_by:
            groups = [{"title": False, "checks": checks}]
        else:
            # One table per group; Sr. restarts in every table. Mirrors the demo (sh.quality):
            # "Product" lists products under each picking, "Picking" lists pickings under each product.
            field = "picking_id" if wizard.group_by == "product" else "product_id"
            grouped = {}
            for check in checks:
                grouped.setdefault(check[field], self.env["qc.check"])
                grouped[check[field]] |= check
            groups = [
                {"title": record.display_name or _("Undefined"), "checks": recs}
                for record, recs in sorted(grouped.items(), key=lambda item: item[0].display_name or "")
            ]
        return {"wizard": wizard, "groups": groups, "docs": checks}


class ReportQcAlertSummary(models.AbstractModel):
    _name = "report.quality_control.report_qc_alert_summary"
    _description = "Quality Alert Summary Report"

    @api.model
    def _get_report_values(self, docids, data=None):
        data = data or {}
        wizard = self.env["qc.alert.report.wizard"].browse(data.get("wizard_id"))
        alerts = self.env["qc.alert"].browse(data.get("record_ids", []))
        # One section per Responsible Person; selected users are listed even without alerts.
        users = wizard.user_ids or alerts.user_id
        groups = [{"user": user, "alerts": alerts.filtered(lambda a, u=user: a.user_id == u)}
                  for user in users.sorted("name")]
        unassigned = alerts.filtered(lambda a: not a.user_id)
        if unassigned:
            groups.append({"user": self.env["res.users"], "alerts": unassigned})
        return {"wizard": wizard, "groups": groups, "docs": alerts}
