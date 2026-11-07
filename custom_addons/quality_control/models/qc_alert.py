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
    title = fields.Char()
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)
    stage_id = fields.Many2one(
        "qc.alert.stage", default=_default_stage,
        group_expand="_read_group_stage_ids", tracking=True, ondelete="restrict",
    )
    priority = fields.Selection([("0", "Normal"), ("1", "Low"), ("2", "High"), ("3", "Very High")], default="0")
    tag_ids = fields.Many2many("qc.alert.tag", string="Tags")
    team_id = fields.Many2one("qc.team", tracking=True)
    user_id = fields.Many2one("res.users", string="Responsible", tracking=True, default=lambda self: self.env.user)
    color = fields.Integer()
    partner_id = fields.Many2one("res.partner", string="Vendor/Partner")

    check_id = fields.Many2one("qc.check", string="Quality Check")
    picking_id = fields.Many2one("stock.picking", string="Picking Ref.")
    product_id = fields.Many2one("product.product")
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial")

    description = fields.Html()
    date_close = fields.Datetime(string="Date Closed", readonly=True)
    notes = fields.Text()

    uom_id = fields.Many2one(related="product_id.uom_id", string="UoM")
    pass_qty = fields.Float(
        string="Pass Quantity", compute="_compute_check_qty", store=True, digits=(16, 2),
        help="Taken from the Pass Quantity of the Quality Check.")
    fail_qty = fields.Float(
        string="Fail Quantity", compute="_compute_check_qty", store=True, digits=(16, 2),
        help="Taken from the Fail Quantity of the Quality Check.")
    downgrade_proposed_qty = fields.Float(string="Downgrade (Proposed)")
    scrap_proposed_qty = fields.Float(string="Scrap (Proposed)")
    return_proposed_qty = fields.Float(string="Return (Proposed)")
    transfer_proposed_qty = fields.Float(string="Transfer (Proposed)")
    scrap_ids = fields.One2many("stock.scrap", "qc_alert_id", string="Scraps")
    scrap_executed_qty = fields.Float(string="Scrap (Executed)", compute="_compute_scrap")
    scrap_count = fields.Integer(compute="_compute_scrap")
    conversion_ids = fields.One2many("qc.conversion", "alert_id", string="Simplified MOs")
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
        if self.scrap_ids:
            return self.action_view_scraps()
        if not self.product_id:
            raise UserError(_("Select a product first."))
        if not self.scrap_proposed_qty:
            raise UserError(_("Enter 'Scrap (Proposed)' first."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Scrap"),
            "res_model": "stock.scrap",
            "view_mode": "form",
            "views": [(self.env.ref("stock.stock_scrap_form_view2").id, "form")],
            "target": "new",
            "context": self._prepare_scrap_context(self._scrap_remaining()),
        }

    def _scrap_remaining(self):
        self.ensure_one()
        return self.scrap_proposed_qty - self.scrap_executed_qty

    def _prepare_scrap_vals(self, scrap_qty):
        self.ensure_one()
        vals = {
            "product_id": self.product_id.id,
            "scrap_qty": scrap_qty,
            "qc_alert_id": self.id,
            "origin": self.name,
            "lot_id": self.lot_id.id,
            "picking_id": self.picking_id.id,
            "location_id": self._get_lot_location().id,
        }
        return {key: value for key, value in vals.items() if value}

    def _prepare_scrap_context(self, scrap_qty):
        return {"default_%s" % key: value for key, value in self._prepare_scrap_vals(scrap_qty).items()}

    def _create_draft_scrap(self, qty):
        """Draft Scrap of qty of this alert's lot. Stock moves only when the user validates it."""
        self.ensure_one()
        return self.env["stock.scrap"].create(self._prepare_scrap_vals(qty))

    @api.model
    def _bulk_result_action(self, title, message, skipped):
        """Notification shown after a bulk action; skipped is a list of (alert name, reason)."""
        if skipped:
            message += _(" Skipped: %s", "; ".join("%s (%s)" % item for item in skipped))
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": title,
                "message": message,
                "type": "warning" if skipped else "success",
                "sticky": bool(skipped),
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def action_view_check(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_check_action")
        action.update({"views": [(False, "form")], "res_id": self.check_id.id})
        return action

    def _action_not_available(self):
        raise UserError(_("This action is not available yet. Please use Scrap for now."))

    def _get_lot_location(self):
        """Internal location where the lot sits now; falls back to the picking destination."""
        self.ensure_one()
        quant = self.env["stock.quant"].search([
            ("product_id", "=", self.product_id.id),
            ("lot_id", "=", self.lot_id.id),
            ("location_id.usage", "=", "internal"),
            ("quantity", ">", 0),
        ], limit=1) if self.lot_id else self.env["stock.quant"]
        return quant.location_id or self.picking_id.location_dest_id

    def _downgrade_remaining(self):
        # Proposed is only a suggestion (executed may exceed it), so never block; the user types the rest.
        self.ensure_one()
        return max(self.downgrade_proposed_qty - self.downgrade_executed_qty, 0.0)

    def _prepare_consume_vals(self, qty):
        self.ensure_one()
        return {
            "line_type": "consume",
            "product_id": self.product_id.id,
            "qty": qty,
            "location_id": self._get_lot_location().id,
            "lot_id": self.lot_id.id,
        }

    def _create_draft_conversion(self, qty, result_product, results=None):
        """Draft Simplified MO: consume qty of this alert's lot and make the same qty of result_product.

        results is an optional list of (product, qty) or (product, qty, lot) to make several products
        instead of one. A result without a lot gets the source lot name (Generate Lot).
        Stock moves only when the user validates it. Before that, the user can still edit the draft.
        """
        self.ensure_one()
        results = [tuple(item) + (False,) * (3 - len(item)) for item in (results or [(result_product, qty)])]
        consume_vals = self._prepare_consume_vals(qty)
        location = self.env["stock.location"].browse(consume_vals["location_id"])
        quants = self.env["stock.quant"].search([
            ("product_id", "=", self.product_id.id),
            ("lot_id", "=", self.lot_id.id),
            ("location_id", "child_of", location.id),
        ])
        if sum(quants.mapped("quantity")) < consume_vals["qty"]:
            raise UserError(_("not enough stock in %s", location.display_name))
        conversion = self.env["qc.conversion"].create({
            "alert_id": self.id,
            "origin": self.name,
            "company_id": (self.company_id or self.env.company).id,
            "consume_line_ids": [fields.Command.create(consume_vals)],
            "produce_line_ids": [fields.Command.create({
                "line_type": "produce",
                "product_id": product.id,
                "qty": result_qty,
                "location_id": location.id,
                "lot_id": lot.id if lot else False,
            }) for product, result_qty, lot in results],
        })
        conversion.action_generate_lot()
        return conversion

    def _active_conversion(self):
        """The alert's Simplified MO (Cancelled ones do not count)."""
        self.ensure_one()
        return self.conversion_ids.filtered(lambda c: c.state != "cancel").sorted("id")[:1]

    def action_create_downgrade(self):
        self.ensure_one()
        conversion = self._active_conversion()
        if conversion:
            return {
                "type": "ir.actions.act_window",
                "res_model": "qc.conversion",
                "view_mode": "form",
                "res_id": conversion.id,
            }
        if not self.product_id:
            raise UserError(_("Select a product first."))
        consume_vals = self._prepare_consume_vals(self._downgrade_remaining())
        return {
            "type": "ir.actions.act_window",
            "res_model": "qc.conversion",
            "view_mode": "form",
            "target": "current",
            "context": {
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
        if len(self.scrap_ids) == 1:
            action.update({"views": [(False, "form")], "res_id": self.scrap_ids.id})
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

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            alert = self.env["qc.alert"].browse(vals.get("qc_alert_id"))
            if alert.scrap_ids:
                raise UserError(_("%s already has a Scrap document.", alert.name))
        return super().create(vals_list)
