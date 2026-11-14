from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero

BOOKING_STATES = [
    ("draft", "Draft"),
    ("to_approve", "To Approve"),
    ("booked", "Booked"),
    ("closed", "Closed"),
    ("released", "Released"),
    ("reject", "Rejected"),
    ("cancel", "Cancelled"),
]


class SaleBooking(models.Model):
    _name = "mst.sale.booking"
    _description = "Sales Booking"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Booking Number", default="New", readonly=True, copy=False)
    inventory_type = fields.Selection([("fg", "Finished Goods"), ("rm", "Greige")], string="Inventory Type",
                                      required=True, default="fg", tracking=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)
    user_id = fields.Many2one("res.users", string="Salesperson", default=lambda self: self.env.user, tracking=True)
    team_id = fields.Many2one("crm.team", string="Sales Team", compute="_compute_team_id", store=True,
                              readonly=False)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    warehouse_id = fields.Many2one(
        "stock.warehouse", required=True, check_company=True,
        default=lambda self: self.env["stock.warehouse"].search([("company_id", "=", self.env.company.id)], limit=1))
    booking_date = fields.Datetime(default=fields.Datetime.now, readonly=True, copy=False)
    expiry_date = fields.Date(string="Valid Until", required=True, tracking=True,
                              default=lambda self: self._default_expiry_date())
    state = fields.Selection(BOOKING_STATES, default="draft", required=True, tracking=True, copy=False)
    line_ids = fields.One2many("mst.sale.booking.line", "booking_id", string="Booking Lines", copy=True)
    sale_ids = fields.One2many("sale.order", "booking_id", string="Sales Orders")
    sale_count = fields.Integer(compute="_compute_sale_count")
    note = fields.Text()

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
                code = "mst.sale.booking.%s" % vals.get("inventory_type", "fg")
                vals["name"] = self.env["ir.sequence"].next_by_code(code) or "New"
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

    def action_cancel(self):
        self._check_state("draft", "to_approve")
        self.write({"state": "cancel"})

    def action_draft(self):
        self._check_state("to_approve", "reject", "cancel")
        self.write({"state": "draft"})

    def action_release(self):
        self._check_state("booked")
        self.line_ids._release()
        self.write({"state": "released"})

    def action_view_sales(self):
        action = self.env["ir.actions.act_window"]._for_xml_id("sale.action_orders")
        action["domain"] = [("booking_id", "in", self.ids)]
        action["context"] = {"create": False}
        return action

    def _book(self):
        self.line_ids._hold_stock()
        self.write({"state": "booked"})

    def _utilize(self, sale_lines):
        """Use the booked quantity for the given order lines, product by product."""
        self.ensure_one()
        for sale_line in sale_lines.filtered(lambda l: l.product_id.type == "product"):
            need = sale_line.product_uom_qty
            for line in self.line_ids.filtered(lambda b: b.product_id == sale_line.product_id):
                qty = min(sale_line.product_uom._compute_quantity(need, line.product_uom), line.remaining_qty)
                if float_compare(qty, 0, precision_rounding=line.product_uom.rounding) <= 0:
                    continue
                line._utilize(qty)
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
    _description = "Sales Booking Line"

    booking_id = fields.Many2one("mst.sale.booking", required=True, ondelete="cascade", index=True)
    state = fields.Selection(related="booking_id.state")
    company_id = fields.Many2one(related="booking_id.company_id", store=True)
    product_id = fields.Many2one("product.product", required=True, check_company=True,
                                 domain="[('type', '=', 'product')]")
    product_uom_qty = fields.Float(string="Booked Qty", required=True, default=1.0,
                                   digits="Product Unit of Measure")
    product_uom = fields.Many2one("uom.uom", string="UoM", required=True, compute="_compute_product_uom",
                                  store=True, readonly=False, precompute=True,
                                  domain="[('category_id', '=', product_uom_category_id)]")
    product_uom_category_id = fields.Many2one(related="product_id.uom_id.category_id")
    utilized_qty = fields.Float(string="Utilized Qty", readonly=True, copy=False, digits="Product Unit of Measure")
    released_qty = fields.Float(string="Released Qty", readonly=True, copy=False, digits="Product Unit of Measure")
    remaining_qty = fields.Float(string="Remaining Qty", compute="_compute_remaining_qty",
                                 digits="Product Unit of Measure")
    move_id = fields.Many2one("stock.move", string="Stock Hold", readonly=True, copy=False)

    _sql_constraints = [
        ("qty_positive", "CHECK(product_uom_qty > 0)", "The booked quantity must be positive."),
    ]

    @api.depends("product_id")
    def _compute_product_uom(self):
        for line in self:
            line.product_uom = line.product_id.uom_id

    @api.depends("product_uom_qty", "utilized_qty", "released_qty")
    def _compute_remaining_qty(self):
        for line in self:
            line.remaining_qty = line.product_uom_qty - line.utilized_qty - line.released_qty

    def _hold_stock(self):
        """Reserve the booked quantity in the warehouse stock with a move that is never done."""
        dest = self.env.ref("mst17_sales_booking.stock_location_booking")
        moves = self.env["stock.move"].sudo().create([{
            "name": line.booking_id.name,
            "origin": line.booking_id.name,
            "product_id": line.product_id.id,
            "product_uom_qty": line.product_uom_qty,
            "product_uom": line.product_uom.id,
            "location_id": line.booking_id.warehouse_id.lot_stock_id.id,
            "location_dest_id": dest.id,
            "company_id": line.company_id.id,
        } for line in self])
        moves._action_confirm(merge=False)
        moves._action_assign()
        short = [
            "%s: %s / %s %s" % (move.product_id.display_name, move.quantity, move.product_uom_qty,
                                move.product_uom.name)
            for move in moves
            if float_compare(move.quantity, move.product_uom_qty, precision_rounding=move.product_uom.rounding) < 0
        ]
        if short:
            raise UserError(_("Not enough free stock to book (free / asked):\n%s", "\n".join(short)))
        for line, move in zip(self, moves):
            line.move_id = move

    def _utilize(self, qty):
        self.ensure_one()
        self.utilized_qty += qty
        move = self.move_id.sudo()
        move._do_unreserve()
        if float_is_zero(self.remaining_qty, precision_rounding=self.product_uom.rounding):
            move._action_cancel()
        else:
            move.product_uom_qty = self.remaining_qty
            move._action_assign()

    def _release(self):
        for line in self:
            line.released_qty += line.remaining_qty
        self.move_id.sudo().filtered(lambda m: m.state not in ("done", "cancel"))._action_cancel()
