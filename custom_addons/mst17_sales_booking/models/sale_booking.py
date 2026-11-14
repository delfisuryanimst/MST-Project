from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

BOOKING_STATES = [
    ("draft", "Draft"),
    ("to_approve", "To Approve"),
    ("booked", "Booked"),
    ("closed", "Closed"),
    ("reject", "Rejected"),
    ("cancel", "Cancelled"),
]


class SaleBooking(models.Model):
    """Booking Form: greige (raw material) booked with approval. Finished goods use Book Stock."""
    _name = "mst.sale.booking"
    _description = "Booking Form"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Booking Number", default="New", readonly=True, copy=False)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)
    user_id = fields.Many2one("res.users", string="Salesperson", default=lambda self: self.env.user, tracking=True)
    team_id = fields.Many2one("crm.team", string="Sales Team", compute="_compute_team_id", store=True,
                              readonly=False)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1))
    booking_date = fields.Datetime(default=fields.Datetime.now, readonly=True, copy=False)
    booking_approval_date = fields.Datetime(string="Booking Approval Date", readonly=True, copy=False)
    expiry_date = fields.Date(string="Booking Expiry Date", required=True, tracking=True,
                              default=lambda self: self._default_expiry_date())
    state = fields.Selection(BOOKING_STATES, string="Status", default="draft", required=True, tracking=True,
                             copy=False)
    line_ids = fields.One2many("mst.sale.booking.line", "booking_id", string="Booking Lines", copy=True)
    sale_ids = fields.One2many("sale.order", "booking_id", string="Sales Orders")
    sale_count = fields.Integer(compute="_compute_sale_count")
    note = fields.Text(string="Remarks")

    @api.model
    def _default_expiry_date(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param("mst17_sales_booking.validity_days", 30))
        return fields.Date.context_today(self) + timedelta(days=days)

    @api.depends("user_id")
    def _compute_team_id(self):
        for booking in self:
            booking.team_id = booking.user_id.sale_team_id

    def _compute_sale_count(self):
        for booking in self:
            booking.sale_count = len(booking.sale_ids)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("mst.sale.booking.rm") or "New"
        return super().create(vals_list)

    @api.ondelete(at_uninstall=False)
    def _unlink_only_draft(self):
        if any(booking.state not in ("draft", "cancel") for booking in self):
            raise UserError(_("Only draft or cancelled bookings can be deleted."))

    def _check_state(self, *states):
        if any(booking.state not in states for booking in self):
            raise UserError(_("This action is not allowed in the current status."))

    def action_confirm(self):
        self._check_state("draft")
        if any(not booking.line_ids for booking in self):
            raise UserError(_("Add at least one product to book."))
        if self.env.user.has_group("sales_team.group_sale_manager"):
            self._book()
        else:
            self.write({"state": "to_approve"})

    def action_approve(self):
        self._check_state("to_approve")
        if not self.env.user.has_group("sales_team.group_sale_manager"):
            raise UserError(_("Only a Sales Manager can approve a booking."))
        self._book()

    def action_reject(self):
        self._check_state("to_approve")
        self.write({"state": "reject"})
        self._unlink_quotations()

    def action_cancel(self):
        self._check_state("draft", "to_approve")
        self.write({"state": "cancel"})
        self._unlink_quotations()

    def action_draft(self):
        self._check_state("to_approve", "reject", "cancel")
        self.write({"state": "draft"})

    def action_release(self):
        self._check_state("booked")
        self.line_ids._release()
        self.write({"state": "closed"})
        self._unlink_quotations()

    def _unlink_quotations(self):
        """A booking that no longer holds stock is removed from quotations, so they can book again."""
        self.sale_ids.sudo().filtered(lambda o: o.state in ("draft", "sent")).booking_id = False

    def action_view_sales(self):
        action = self.env["ir.actions.act_window"]._for_xml_id("sale.action_orders")
        action["domain"] = [("booking_id", "in", self.ids)]
        action["context"] = {"create": False}
        return action

    def _book(self):
        self.line_ids._hold_stock()
        self.write({"state": "booked", "booking_approval_date": fields.Datetime.now()})

    def _utilize(self, sale_lines):
        """Use the booked quantity for the given order lines, product by product."""
        self.ensure_one()
        for sale_line in sale_lines.filtered(lambda l: l.product_id.type == "product"):
            need = sale_line.product_uom_qty
            for line in self.line_ids.filtered(lambda b: b.product_id == sale_line.product_id):
                qty = min(sale_line.product_uom._compute_quantity(need, line.product_uom), line.remaining_qty)
                if float_compare(qty, 0, precision_rounding=line.product_uom.rounding) <= 0:
                    continue
                line.used_qty += qty
                line._resize_hold()
                need -= line.product_uom._compute_quantity(qty, sale_line.product_uom)
        if all(float_is_zero(line.remaining_qty, precision_rounding=line.product_uom.rounding)
               for line in self.line_ids):
            self.state = "closed"

    @api.model
    def _cron_release_expired(self):
        expired = self.search([("state", "=", "booked"), ("expiry_date", "<", fields.Date.context_today(self))])
        for booking in expired:
            booking.action_release()
            booking.message_post(body=_("Released automatically: the booking expired."))


class SaleBookingLine(models.Model):
    _name = "mst.sale.booking.line"
    _inherit = "mst.stock.hold.mixin"
    _description = "Booking Form Line"

    booking_id = fields.Many2one("mst.sale.booking", string="Booking Number", required=True, ondelete="cascade",
                                 index=True)
    company_id = fields.Many2one(related="booking_id.company_id", store=True)
    state = fields.Selection(related="booking_id.state", string="Status")
    booking_date = fields.Datetime(related="booking_id.booking_date")
    expiry_date = fields.Date(related="booking_id.expiry_date")
    partner_id = fields.Many2one(related="booking_id.partner_id", string="Customer")
    user_id = fields.Many2one(related="booking_id.user_id", string="Salesperson")
    note = fields.Text(related="booking_id.note", string="Remarks")

    _sql_constraints = [
        ("qty_positive", "CHECK(product_uom_qty > 0)", "The booked quantity must be positive."),
    ]

    def _compute_display_name(self):
        for line in self:
            line.display_name = line.booking_id.name

    def _hold_location(self):
        return self.booking_id.warehouse_id.lot_stock_id

    def _release(self):
        for line in self:
            line.released_qty += line.remaining_qty
        self._resize_hold()
