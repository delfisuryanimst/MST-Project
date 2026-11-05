from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from .qc_point import TEST_TYPES

QUALITY_STATES = [("todo", "To Do"), ("pass", "Passed"), ("fail", "Failed")]
GRADING_DIGITS = (16, 8)
QTY_DIGITS = (16, 2)  # FDD: field qty QC Form 2 desimal


class QcCheck(models.Model):
    _name = "qc.check"
    _description = "Quality Check"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Name", default=lambda self: _("New"), copy=False, readonly=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    point_id = fields.Many2one("qc.point", string="QC Point", tracking=True)
    team_id = fields.Many2one("qc.team", string="Team", tracking=True)
    user_id = fields.Many2one("res.users", string="QC User", default=lambda self: self.env.user)

    picking_id = fields.Many2one("stock.picking", string="Picking", tracking=True, index=True)
    picking_type_id = fields.Many2one(related="picking_id.picking_type_id", store=True, string="Operation Type")
    partner_id = fields.Many2one(
        "res.partner", string="Partner", compute="_compute_partner_id", store=True,
        help="Partner of the transfer, or the vendor of its origin receipt.",
    )
    move_line_id = fields.Many2one("stock.move.line", string="Detailed Operation")
    product_id = fields.Many2one("product.product", string="Product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Lot", domain="[('product_id', '=', product_id)]")
    lot_name = fields.Char(string="Lot Name")
    product_uom_id = fields.Many2one(related="product_id.uom_id", string="UoM")

    test_type = fields.Selection(TEST_TYPES, string="Type", default="pass_fail", required=True)
    qty_to_test = fields.Float(string="Quantity to Test")
    quality_state = fields.Selection(
        QUALITY_STATES, string="Stage", default="todo", required=True, tracking=True, copy=False)
    control_date = fields.Datetime(string="Date", readonly=True, copy=False, default=fields.Datetime.now)
    note = fields.Html(string="Message")
    point_note = fields.Html(related="point_id.note", string="Instructions")

    yard_asal = fields.Float(string="Yard Asal")
    yard_counter_qty = fields.Float(
        string="Yard Counter Qty", digits=QTY_DIGITS,
        help="Total hasil inspeksi QC (bisa melebihi Qty to Test).")
    difference_qty = fields.Float(
        string="Difference Qty", compute="_compute_difference_qty", store=True, digits=QTY_DIGITS,
        help="Quantity to Test - Yard Counter Qty",
    )
    pass_qty = fields.Float(string="Pass Quantity", digits=QTY_DIGITS, help="Grade A.")
    qty_to_downgrade = fields.Float(string="Quantity to Downgrade", digits=QTY_DIGITS, help="Grade B.")
    qty_to_scrap = fields.Float(string="Quantity to Scrap", digits=QTY_DIGITS, help="BS")
    fail_qty = fields.Float(string="Fail Quantity", digits=QTY_DIGITS)
    qty_to_sample = fields.Float(
        string="Quantity to Sample", digits=QTY_DIGITS, help="Qty yang dipotong untuk keperluan sample.")
    result = fields.Selection([("pass", "Pass"), ("fail", "Fail")], string="Pass/Fail")

    measure = fields.Float(string="Measure")
    norm_unit = fields.Char(related="point_id.norm_unit")
    tolerance_min = fields.Float(related="point_id.tolerance_min")
    tolerance_max = fields.Float(related="point_id.tolerance_max")

    picture = fields.Image(string="Upload Picture", max_width=1920, max_height=1920)
    text_result = fields.Text(string="Result")
    attachment_ids = fields.Many2many("ir.attachment", string="Attachments")

    lebar_inch = fields.Float(string="Lebar (inch)")
    detail_ids = fields.One2many("qc.check.detail", "check_id", string="Detail Cacat")
    akumulasi_poin = fields.Float(
        string="Akumulasi Poin", compute="_compute_akumulasi_poin", store=True,
        help="Jumlah Nilai Poin dari Detail Cacat.",
    )
    nilai_grading = fields.Float(
        string="Nilai Grading", compute="_compute_nilai_grading", store=True, readonly=False,
        digits=GRADING_DIGITS, help="(Akumulasi Poin x 3600) / (Lebar x Yard Counter Qty)",
    )
    nilai_grading_after = fields.Float(
        string="Nilai Grading (Setelah Cutting)", compute="_compute_nilai_grading_after", store=True,
        readonly=False, digits=GRADING_DIGITS, help="(Akumulasi Poin x 3600) / (Lebar x Pass Quantity)",
    )
    posisi_cacat = fields.Char(string="Posisi Cacat")
    description = fields.Text(string="Description")

    alert_ids = fields.One2many("qc.alert", "check_id", string="Quality Alerts")
    alert_count = fields.Integer(compute="_compute_alert_count")

    # Selection filters on the form: products of the transfer, matching On Demand QC Points
    product_domain_ids = fields.Many2many("product.product", compute="_compute_product_domain_ids")
    point_domain_ids = fields.Many2many("qc.point", compute="_compute_point_domain_ids")

    @api.depends("picking_id.move_ids.product_id")
    def _compute_product_domain_ids(self):
        for check in self:
            check.product_domain_ids = check.picking_id.move_ids.product_id

    @api.depends("picking_type_id", "picking_id.picking_type_id", "product_id", "company_id")
    def _compute_point_domain_ids(self):
        for check in self:
            picking_type = check.picking_id.picking_type_id or check.picking_type_id
            points = self.env["qc.point"]._get_points(picking_type, "on_demand", check.company_id)
            if check.product_id:
                points = points.filtered(lambda p: p._applies_to_product(check.product_id))
            check.point_domain_ids = points

    @api.depends("picking_id.partner_id", "picking_id.move_ids.move_orig_ids.picking_id.partner_id")
    def _compute_partner_id(self):
        for check in self:
            check.partner_id = check.picking_id._get_qc_partner() if check.picking_id else False

    @api.depends("qty_to_test", "yard_counter_qty")
    def _compute_difference_qty(self):
        for check in self:
            check.difference_qty = check.qty_to_test - check.yard_counter_qty

    @api.depends("detail_ids.nilai_poin")
    def _compute_akumulasi_poin(self):
        for check in self:
            check.akumulasi_poin = sum(check.detail_ids.mapped("nilai_poin"))

    @staticmethod
    def _grading(akumulasi_poin, lebar_inch, qty):
        return akumulasi_poin * 3600.0 / (lebar_inch * qty) if lebar_inch and qty else 0.0

    @api.depends("akumulasi_poin", "lebar_inch", "yard_counter_qty")
    def _compute_nilai_grading(self):
        for check in self:
            check.nilai_grading = self._grading(check.akumulasi_poin, check.lebar_inch, check.yard_counter_qty)

    @api.depends("akumulasi_poin", "lebar_inch", "pass_qty")
    def _compute_nilai_grading_after(self):
        for check in self:
            check.nilai_grading_after = self._grading(check.akumulasi_poin, check.lebar_inch, check.pass_qty)

    @api.depends("alert_ids")
    def _compute_alert_count(self):
        for check in self:
            check.alert_count = len(check.alert_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("qc.check") or _("New")
        return super().create(vals_list)

    def unlink(self):
        if not self.env.user.has_group("quality_control.group_qc_manager"):
            raise UserError(_("Hanya QC Administrator yang dapat menghapus QC Form."))
        return super().unlink()

    @api.onchange("point_id")
    def _onchange_point_id(self):
        if self.point_id:
            self.test_type = self.point_id.test_type
            self.team_id = self.point_id._get_team()
            if self.point_id.user_id:
                self.user_id = self.point_id.user_id

    def do_pass(self):
        self.write({"quality_state": "pass", "result": "pass", "control_date": fields.Datetime.now()})

    def do_fail(self):
        self.write({"quality_state": "fail", "result": "fail", "control_date": fields.Datetime.now()})

    def do_measure(self):
        for check in self:
            if check.tolerance_min <= check.measure <= check.tolerance_max:
                check.do_pass()
            else:
                check.do_fail()

    def _recompute_grading(self):
        """TSD: Validate menghitung ulang Nilai Grading dari data terakhir (nilai yang diubah manual ditimpa)."""
        for check in self:
            check.write({
                "nilai_grading": self._grading(check.akumulasi_poin, check.lebar_inch, check.yard_counter_qty),
                "nilai_grading_after": self._grading(check.akumulasi_poin, check.lebar_inch, check.pass_qty),
            })

    def action_validate(self):
        todo = self.filtered(lambda c: c.quality_state == "todo")
        missing = todo.filtered(lambda c: c.test_type == "pass_fail" and not c.result)
        if missing:
            raise UserError(_(
                "Pilih Pass/Fail terlebih dahulu sebelum Validate:\n%s",
                "\n".join("- %s" % c.name for c in missing)))
        for check in todo:
            check._recompute_grading()
            if check.test_type == "pass_fail" and check.result == "fail":
                check.do_fail()
            elif check.test_type == "pass_fail":
                check.do_pass()
            elif check.test_type == "measure":
                check.do_measure()
            else:
                check._check_result_filled()
                check.do_pass()
        return True

    def _check_result_filled(self):
        self.ensure_one()
        if self.test_type == "picture" and not self.picture:
            raise UserError(_("Silakan upload gambar terlebih dahulu (%s).", self.name))
        if self.test_type == "text" and not self.text_result:
            raise UserError(_("Silakan isi hasil pengecekan terlebih dahulu (%s).", self.name))

    def action_reset(self):
        self.write({"quality_state": "todo"})

    def _prepare_alert_vals(self):
        self.ensure_one()
        # Title is typed by the user (demo: required, no default), e.g. "SCRAP", "DOWNGRADE".
        return {
            "check_id": self.id,
            "picking_id": self.picking_id.id,
            "partner_id": self.partner_id.id,
            "product_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "team_id": self.team_id.id,
            "user_id": self.user_id.id or self.env.user.id,
            "downgrade_proposed_qty": self.qty_to_downgrade,
            "scrap_proposed_qty": self.qty_to_scrap,
            "description": self.note or (self.description and "<p>%s</p>" % self.description),
        }

    def action_create_alert(self):
        """Create a Quality Alert straight from a failed check (demo: 'Quality Alerts' button)."""
        self.ensure_one()
        if self.quality_state != "fail":
            raise UserError(_("Quality Alert hanya bisa dibuat dari Quality Check yang Failed."))
        if self.alert_ids:
            return self.action_view_alerts()
        # Open an unsaved, prefilled form so the user must type the Title before saving.
        return {
            "type": "ir.actions.act_window",
            "name": _("Quality Alert"),
            "res_model": "qc.alert",
            "view_mode": "form",
            "views": [(False, "form")],
            "context": {"default_%s" % key: value for key, value in self._prepare_alert_vals().items()},
        }

    def action_view_alerts(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_alert_action")
        action["domain"] = [("check_id", "=", self.id)]
        return action

    def action_print(self):
        return self.env.ref("quality_control.action_report_qc_check").report_action(self)


class QcCheckDetail(models.Model):
    _name = "qc.check.detail"
    _description = "Quality Check - Detail Cacat"
    _order = "check_id, yard_ke, id"

    check_id = fields.Many2one("qc.check", string="Quality Check", required=True, ondelete="cascade", index=True)
    posisi_cacat = fields.Char(string="Posisi Cacat")
    yard_ke = fields.Float(string="Yard Ke")
    no_cacat = fields.Float(string="No. Cacat")
    nilai_poin = fields.Float(string="Nilai Poin", help="4 Point System: 1 - 4 poin per cacat")
    remarks = fields.Char(string="Remarks")

    @api.constrains("nilai_poin")
    def _check_nilai_poin(self):
        for detail in self:
            if not 0 <= detail.nilai_poin <= 4:
                raise ValidationError(_("Nilai Poin per cacat harus antara 0 dan 4 (4 Point System)."))
