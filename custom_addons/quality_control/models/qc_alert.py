from odoo import _, api, fields, models
from odoo.exceptions import UserError


class QcAlert(models.Model):
    _name = "qc.alert"
    _description = "Quality Alert"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "priority desc, id desc"

    def _default_stage(self):
        return self.env["qc.alert.stage"].search([], limit=1)

    name = fields.Char(string="Reference", default=lambda self: _("New"), copy=False, readonly=True)
    title = fields.Char(string="Title")
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    stage_id = fields.Many2one(
        "qc.alert.stage", string="Stage", default=_default_stage,
        group_expand="_read_group_stage_ids", tracking=True, ondelete="restrict",
    )
    priority = fields.Selection(
        [("0", "Normal"), ("1", "Low"), ("2", "High"), ("3", "Very High")], default="0")
    tag_ids = fields.Many2many("qc.alert.tag", string="Tags")
    team_id = fields.Many2one("qc.team", string="Team", tracking=True)
    user_id = fields.Many2one("res.users", string="Responsible", tracking=True,
                              default=lambda self: self.env.user)
    color = fields.Integer()
    partner_id = fields.Many2one("res.partner", string="Vendor/Partner")

    check_id = fields.Many2one("qc.check", string="Quality Check")
    picking_id = fields.Many2one("stock.picking", string="Picking Ref.")
    product_id = fields.Many2one("product.product", string="Product")
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial")

    description = fields.Html(string="Description")
    date_close = fields.Datetime(string="Date Closed", readonly=True)
    notes = fields.Text(string="Notes")

    # Quantities & follow-up
    uom_id = fields.Many2one(related="product_id.uom_id", string="UoM")
    pass_qty = fields.Float(
        string="Pass Quantity", compute="_compute_check_qty", store=True, digits=(16, 2),
        help="Diambil dari Pass Quantity di Quality Check.")
    fail_qty = fields.Float(
        string="Fail Quantity", compute="_compute_check_qty", store=True, digits=(16, 2),
        help="Diambil dari Fail Quantity di Quality Check.")
    downgrade_proposed_qty = fields.Float(string="Downgrade (Proposed)")
    scrap_proposed_qty = fields.Float(string="Scrap (Proposed)")
    return_proposed_qty = fields.Float(string="Return (Proposed)")
    transfer_proposed_qty = fields.Float(string="Transfer (Proposed)")
    scrap_ids = fields.One2many("stock.scrap", "qc_alert_id", string="Scraps")
    scrap_executed_qty = fields.Float(string="Scrap (Executed)", compute="_compute_scrap")
    scrap_count = fields.Integer(compute="_compute_scrap")
    conversion_ids = fields.One2many("qc.conversion", "alert_id", string="Stock Conversions")
    downgrade_executed_qty = fields.Float(string="Downgrade (Executed)", compute="_compute_downgrade")
    conversion_count = fields.Integer(compute="_compute_downgrade")

    @api.depends("check_id.pass_qty", "check_id.fail_qty")
    def _compute_check_qty(self):
        for alert in self:
            alert.pass_qty = alert.check_id.pass_qty
            alert.fail_qty = alert.check_id.fail_qty

    @api.depends("conversion_ids.state", "conversion_ids.consume_line_ids.qty")
    def _compute_downgrade(self):
        for alert in self:
            done = alert.conversion_ids.filtered(lambda c: c.state == "done")
            alert.downgrade_executed_qty = sum(done.consume_line_ids.mapped("qty"))
            alert.conversion_count = len(alert.conversion_ids)

    @api.depends("scrap_ids.scrap_qty", "scrap_ids.state")
    def _compute_scrap(self):
        for alert in self:
            done_scraps = alert.scrap_ids.filtered(lambda s: s.state == "done")
            alert.scrap_executed_qty = sum(done_scraps.mapped("scrap_qty"))
            alert.scrap_count = len(alert.scrap_ids)

    @api.onchange("check_id")
    def _onchange_check_id(self):
        check = self.check_id
        if check:
            self.picking_id = check.picking_id
            self.product_id = check.product_id
            self.lot_id = check.lot_id
            self.team_id = check.team_id

    def action_create_scrap(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_("Isi Product terlebih dahulu."))
        if not self.scrap_proposed_qty:
            raise UserError(_("Isi 'Scrap (Proposed)' terlebih dahulu."))
        remaining = self.scrap_proposed_qty - self.scrap_executed_qty
        if remaining <= 0:
            raise UserError(_("Qty scrap sudah mencapai atau melebihi 'Scrap (Proposed)'. Tidak ada sisa yang perlu di-scrap."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Scrap"),
            "res_model": "stock.scrap",
            "view_mode": "form",
            "views": [(self.env.ref("stock.stock_scrap_form_view2").id, "form")],
            "target": "new",
            "context": self._prepare_scrap_context(remaining),
        }

    def _prepare_scrap_context(self, scrap_qty):
        self.ensure_one()
        context = {
            "default_product_id": self.product_id.id,
            "default_scrap_qty": scrap_qty,
            "default_qc_alert_id": self.id,
            "default_origin": self.name,
        }
        if self.lot_id:
            context["default_lot_id"] = self.lot_id.id
        if self.picking_id:
            context["default_picking_id"] = self.picking_id.id
        location = self._get_lot_location()
        if location:
            context["default_location_id"] = location.id
        return context

    def action_view_check(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_check_action")
        action.update({"views": [(False, "form")], "res_id": self.check_id.id})
        return action

    def _action_not_available(self):
        raise UserError(_("Aksi ini belum tersedia di Odoo lokal. Untuk sementara gunakan Scrap."))

    def _get_lot_location(self):
        """Lokasi internal tempat lot ini berada sekarang; kalau tidak ketemu, lokasi tujuan picking."""
        self.ensure_one()
        quant = self.env["stock.quant"].search([
            ("product_id", "=", self.product_id.id),
            ("lot_id", "=", self.lot_id.id),
            ("location_id.usage", "=", "internal"),
            ("quantity", ">", 0),
        ], limit=1) if self.lot_id else self.env["stock.quant"]
        return quant.location_id or self.picking_id.location_dest_id

    def action_create_downgrade(self):
        self.ensure_one()
        if not self.product_id:
            raise UserError(_("Isi Product terlebih dahulu."))
        # 'Downgrade (Proposed)' is only a suggestion: executed qty may exceed it (e.g. proposed 21, executed 307),
        # so never block. The form starts with the remaining qty (0 when nothing is left) and the user types the rest.
        remaining = max(self.downgrade_proposed_qty - self.downgrade_executed_qty, 0.0)
        consume_vals = {
            "line_type": "consume",
            "product_id": self.product_id.id,
            "qty": remaining,
            "location_id": self._get_lot_location().id,
            "lot_id": self.lot_id.id,
        }
        return {
            "type": "ir.actions.act_window",
            "name": _("Downgrade"),
            "res_model": "qc.conversion",
            "view_mode": "form",
            "target": "current",
            "context": {
                "default_name": _("Downgrade"),
                "default_alert_id": self.id,
                "default_origin": self.name,
                "default_company_id": (self.company_id or self.env.company).id,
                "default_consume_line_ids": [fields.Command.create(consume_vals)],
            },
        }

    def action_view_conversions(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_conversion_action")
        action["domain"] = [("alert_id", "=", self.id)]
        action["context"] = {"create": False}
        if len(self.conversion_ids) == 1:
            action.update({"views": [(False, "form")], "res_id": self.conversion_ids.id})
        return action

    def action_create_return(self):
        self._action_not_available()

    def action_create_transfer(self):
        self._action_not_available()

    def action_view_scraps(self):
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_stock_scrap")
        action["domain"] = [("qc_alert_id", "in", self.ids)]
        action["context"] = {"create": False}
        return action

    @api.model
    def _read_group_stage_ids(self, stages, domain, order):
        return stages.search([], order=order)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("qc.alert") or _("New")
        return super().create(vals_list)

    def write(self, vals):
        if "stage_id" in vals:
            stage = self.env["qc.alert.stage"].browse(vals["stage_id"])
            vals["date_close"] = fields.Datetime.now() if stage.done else False
        return super().write(vals)


class StockScrap(models.Model):
    _inherit = "stock.scrap"

    qc_alert_id = fields.Many2one("qc.alert", string="Quality Alert", index=True, copy=False)
