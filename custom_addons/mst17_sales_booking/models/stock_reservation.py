from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare


class StockReservation(models.Model):
    """Booking Stock: free stock held for one quotation line ("Book Stock"), no approval."""
    _name = "mst.stock.reservation"
    _inherit = "mst.stock.hold.mixin"
    _description = "Stock Reservation"
    _order = "id desc"

    name = fields.Char(string="Number", default="New", readonly=True, copy=False)
    sale_order_line_id = fields.Many2one("sale.order.line", string="Sales Quotation Line", required=True,
                                         ondelete="cascade", index=True)
    sale_order_id = fields.Many2one(related="sale_order_line_id.order_id", string="Sales Quotation",
                                    store=True, index=True)
    partner_id = fields.Many2one(related="sale_order_id.partner_id", string="Owner", store=True)
    user_id = fields.Many2one(related="sale_order_id.user_id", string="Salesperson", store=True)
    company_id = fields.Many2one(related="sale_order_id.company_id", store=True)
    location_id = fields.Many2one("stock.location", string="Source Location", required=True,
                                  compute="_compute_location_id", store=True, readonly=False, precompute=True)
    location_dest_id = fields.Many2one(related="move_id.location_dest_id", string="Destination Location")
    date_validity = fields.Date(string="Validity Date", required=True,
                                default=lambda self: self._default_date_validity())
    booking_status = fields.Selection([("booked", "Booked"), ("released", "Released"), ("done", "Done")],
                                      default="booked", required=True, readonly=True, copy=False)
    used_qty = fields.Float(string="Utilized Qty")
    note = fields.Text(string="Notes")

    @api.model
    def _default_date_validity(self):
        days = int(self.env["ir.config_parameter"].sudo().get_param("mst17_sales_booking.validity_days", 30))
        return fields.Date.context_today(self) + timedelta(days=days)

    @api.depends("sale_order_line_id")
    def _compute_location_id(self):
        for rec in self:
            rec.location_id = rec.sale_order_line_id.order_id.warehouse_id.lot_stock_id

    def _hold_location(self):
        return self.location_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", "New") == "New":
                vals["name"] = self.env["ir.sequence"].next_by_code("mst.stock.reservation") or "New"
        return super().create(vals_list)

    def _check_booked(self):
        if any(rec.booking_status != "booked" for rec in self):
            raise UserError(_("Only booked reservations can be released."))

    def open_release_wizard(self):
        self._check_booked()
        return {
            "type": "ir.actions.act_window",
            "name": _("Release Booking"),
            "res_model": "mst.stock.reservation.release",
            "view_mode": "form",
            "target": "new",
            "context": {"default_reservation_id": self.id, "default_qty": self.remaining_qty},
        }

    def _release(self, qty):
        self.ensure_one()
        self._check_booked()
        if float_compare(qty, 0, precision_rounding=self.product_uom.rounding) <= 0 \
                or float_compare(qty, self.remaining_qty, precision_rounding=self.product_uom.rounding) > 0:
            raise UserError(_("The quantity to release must be between 0 and %s.", self.remaining_qty))
        self.released_qty += qty
        self._resize_hold()
        if self.move_id.state == "cancel":
            self.booking_status = "released"

    def _release_all(self):
        for rec in self:
            rec.released_qty += rec.remaining_qty
        self._resize_hold()
        self.booking_status = "released"

    def _utilize(self):
        """On order confirmation: use what the line needs, free the rest for the delivery."""
        for rec in self:
            line = rec.sale_order_line_id
            used = min(line.product_uom._compute_quantity(line.product_uom_qty, rec.product_uom), rec.remaining_qty)
            rec.write({
                "used_qty": rec.used_qty + used,
                "released_qty": rec.released_qty + rec.remaining_qty - used,
                "booking_status": "done",
            })
        self._resize_hold()

    def open_move(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.move",
            "res_id": self.move_id.id,
            "view_mode": "form",
        }

    @api.model
    def _cron_release_expired(self):
        self.search([("booking_status", "=", "booked"),
                     ("date_validity", "<", fields.Date.context_today(self))])._release_all()
