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
    qty_adjust_proposed_qty = fields.Float(
        string="Qty Correction (Proposed)",
        help="Set when the Pass Quantity of the check differs from its Quantity to Test (downgrade, scrap, "
             "sample or a measuring difference): the Pass Quantity is the qty the lot should keep.")
    qty_adjust_executed_qty = fields.Float(string="Qty Correction (Executed)", compute="_compute_downgrade")
    scrap_proposed_qty = fields.Float(string="Scrap (Proposed)")
    return_proposed_qty = fields.Float(string="Return (Proposed)")
    transfer_proposed_qty = fields.Float(string="Transfer (Proposed)")
    scrap_ids = fields.One2many("stock.scrap", "qc_alert_id", string="Scraps")
    scrap_executed_qty = fields.Float(string="Scrap (Executed)", compute="_compute_scrap")
    scrap_count = fields.Integer(compute="_compute_scrap")
    conversion_ids = fields.One2many("qc.conversion", "alert_id", string="Simplified MOs")
    downgrade_executed_qty = fields.Float(string="Downgrade (Executed)", compute="_compute_downgrade")
    downgrade_count = fields.Integer(compute="_compute_downgrade")
    qty_adjust_count = fields.Integer(compute="_compute_downgrade")

    @api.depends("check_id.pass_qty", "check_id.fail_qty")
    def _compute_check_qty(self):
        for alert in self:
            alert.pass_qty = alert.check_id.pass_qty
            alert.fail_qty = alert.check_id.fail_qty

    @api.depends("conversion_ids.state", "conversion_ids.conversion_type", "conversion_ids.consume_line_ids.qty")
    def _compute_downgrade(self):
        for alert in self:
            # Executed = what the done Downgrade MOs consumed (To be Consumed). Qty Corrections do not count.
            done = alert._downgrade_conversions().filtered(lambda c: c.state == "done")
            alert.downgrade_executed_qty = sum(done.consume_line_ids.mapped("qty"))
            adjusted = alert.conversion_ids.filtered(
                lambda c: c.conversion_type == "qty_adjust" and c.state == "done")
            alert.qty_adjust_executed_qty = sum(adjusted.produce_line_ids.mapped("qty"))
            alert.downgrade_count = len(alert._downgrade_conversions())
            alert.qty_adjust_count = len(alert.conversion_ids) - alert.downgrade_count

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

    def _get_lot_stock_qty(self):
        """Qty of this alert's lot now in stock, in the place where the lot sits."""
        self.ensure_one()
        if not self.lot_id:
            return 0.0
        location = self._get_lot_location()
        quants = self.env["stock.quant"].search([
            ("product_id", "=", self.product_id.id),
            ("lot_id", "=", self.lot_id.id),
            ("location_id", "child_of", location.id),
        ])
        return sum(quants.mapped("quantity"))

    def action_open_qty_adjust(self):
        """Open the first Qty Correction of this alert. When there is none, open a new one, filled in (it is
        saved when the user saves it). More Qty Corrections are made with New in the list of the smart button."""
        self.ensure_one()
        existing = self.conversion_ids.filtered(lambda c: c.conversion_type == "qty_adjust").sorted("id")[:1]
        if existing:
            return self._open_conversion_form(existing)
        if not self.product_id or not self.lot_id:
            raise UserError(_("The alert needs a product and a lot for a Qty Correction."))
        return {
            "type": "ir.actions.act_window",
            "name": _("Qty Correction"),
            "res_model": "qc.conversion",
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "current",
            "context": self._get_conversion_defaults("qty_adjust"),
        }

    def _get_conversion_defaults(self, conversion_type):
        """Defaults of a new Simplified MO made for this alert (from a button, or New in the list)."""
        self.ensure_one()
        context = {
            "default_alert_id": self.id,
            "default_origin": self.name,
            "default_company_id": (self.company_id or self.env.company).id,
            "default_conversion_type": conversion_type,
            "default_name": self._get_conversion_name(conversion_type),
        }
        if conversion_type == "qty_adjust":
            if self.product_id and self.lot_id:
                # New Qty is the right qty (Pass). Old Qty is what is taken out: the same qty, but never more
                # than the stock of the lot (a roll that came longer than its label gets its qty raised).
                stock_qty = self._get_lot_stock_qty()
                new_qty = self.qty_adjust_proposed_qty or self.pass_qty
                old_qty = min(new_qty, stock_qty) if new_qty else stock_qty
                consume_vals = self._prepare_consume_vals(old_qty)
                context["default_consume_line_ids"] = [fields.Command.create(consume_vals)]
                context["default_produce_line_ids"] = [
                    fields.Command.create(dict(consume_vals, line_type="produce", qty=new_qty))]
        elif self.product_id:
            consume_vals = self._prepare_consume_vals(self._downgrade_remaining())
            context["default_consume_line_ids"] = [fields.Command.create(consume_vals)]
        return context

    def _downgrade_remaining(self):
        # Proposed is only a suggestion (executed may exceed it), so never block; the user types the rest.
        self.ensure_one()
        # Proposed is compared with what was produced (To be Produced), not with what was consumed.
        done = self._downgrade_conversions().filtered(lambda c: c.state == "done")
        produced = sum(done.produce_line_ids.mapped("qty"))
        return max(self.downgrade_proposed_qty - produced, 0.0)

    def _prepare_consume_vals(self, qty):
        self.ensure_one()
        return {
            "line_type": "consume",
            "product_id": self.product_id.id,
            "qty": qty,
            "location_id": self._get_lot_location().id,
            "lot_id": self.lot_id.id,
        }

    def _create_draft_conversion(self, qty, result_product, results=None, conversion_type="downgrade"):
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
            "conversion_type": conversion_type,
            "name": self._get_conversion_name(conversion_type),
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

    @api.model
    def _get_auto_title(self, downgrade_qty=0.0, scrap_qty=0.0, qty_adjust_qty=0.0):
        """Title from the actions that are proposed: 'DOWNGRADE, SCRAP DAN RUBAH QTY', 'SCRAP DAN RUBAH QTY'..."""
        parts = [label for label, qty in (
            ("DOWNGRADE", downgrade_qty), ("SCRAP", scrap_qty), ("RUBAH QTY", qty_adjust_qty)) if qty > 0]
        if not parts:
            return "QUALITY ALERT"
        return parts[0] if len(parts) == 1 else "%s DAN %s" % (", ".join(parts[:-1]), parts[-1])

    @api.onchange("downgrade_proposed_qty", "scrap_proposed_qty", "qty_adjust_proposed_qty")
    def _onchange_auto_title(self):
        """An empty Title follows the proposed actions. A Title that was typed is left alone."""
        for alert in self.filtered(lambda a: not a.title):
            alert.title = alert._get_auto_title(
                alert.downgrade_proposed_qty, alert.scrap_proposed_qty, alert.qty_adjust_proposed_qty)

    def _get_conversion_name(self, conversion_type):
        """Name of a Simplified MO: the type and the QCP, like 'RUBAH QTY QCP 12135' for WH/QCP/12135."""
        self.ensure_one()
        label = "RUBAH QTY" if conversion_type == "qty_adjust" else "DOWNGRADE"
        parts = (self.picking_id.name or "").split("/")
        if len(parts) >= 2 and parts[-1].isdigit():
            return "%s %s %s" % (label, parts[-2], parts[-1].lstrip("0") or "0")
        return "%s %s" % (label, self.picking_id.name) if self.picking_id else label

    def _active_conversion(self):
        """The alert's open (draft) Simplified MO. Done and Cancelled ones do not count: an alert can
        have several Simplified MOs, but only one draft at a time."""
        self.ensure_one()
        return self._downgrade_conversions().filtered(lambda c: c.state == "draft").sorted("id")[:1]

    def _downgrade_conversions(self):
        """The Simplified MOs of type Downgrade (not the Qty Corrections)."""
        return self.conversion_ids.filtered(lambda c: c.conversion_type == "downgrade")

    def _has_work_left(self):
        """False when the alert has a Scrap and its Simplified MO is open, or done with nothing left to downgrade."""
        self.ensure_one()
        if not self.scrap_ids:
            return True
        if self._active_conversion():
            return False
        if not self._downgrade_conversions().filtered(lambda c: c.state == "done"):
            return True
        return self._downgrade_remaining() > 0

    @api.model
    def _open_conversion_form(self, conversion):
        return {
            "type": "ir.actions.act_window",
            "res_model": "qc.conversion",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": conversion.id,
        }

    def action_create_downgrade(self):
        """Open the first Downgrade of this alert. When there is none, open a new one, filled in. More
        Downgrades are made with New in the list of the smart button."""
        self.ensure_one()
        existing = self._downgrade_conversions().sorted("id")[:1]
        if existing:
            return self._open_conversion_form(existing)
        if not self.product_id:
            raise UserError(_("Select a product first."))
        return {
            "type": "ir.actions.act_window",
            "res_model": "qc.conversion",
            "view_mode": "form",
            "target": "current",
            "context": self._get_conversion_defaults("downgrade"),
        }

    def _action_view_conversions(self, conversion_type):
        """List of the alert's Simplified MOs of one type. New in the list starts with the same defaults
        as the button of that type."""
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_conversion_action")
        action["name"] = _("Qty Correction") if conversion_type == "qty_adjust" else _("Downgrade")
        action["domain"] = [("alert_id", "=", self.id), ("conversion_type", "=", conversion_type)]
        action["context"] = self._get_conversion_defaults(conversion_type)
        return action

    def action_view_downgrades(self):
        return self._action_view_conversions("downgrade")

    def action_view_qty_adjusts(self):
        return self._action_view_conversions("qty_adjust")

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
