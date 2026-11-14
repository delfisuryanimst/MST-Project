from odoo import _, api, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    booking_id = fields.Many2one(
        "mst.sale.booking", string="Booking Reference", copy=False, tracking=True,
        domain="[('state', '=', 'booked'), ('partner_id', '=', partner_id), ('company_id', '=', company_id)]")
    reservation_ids = fields.One2many("mst.stock.reservation", "sale_order_id", string="Stock Reservations")
    booking_count = fields.Integer(string="Number of Bookings", compute="_compute_booking_count")
    can_book_stock = fields.Boolean(compute="_compute_booking_count")

    @api.depends("state", "order_line.product_id", "reservation_ids.booking_status")
    def _compute_booking_count(self):
        for order in self:
            order.booking_count = len(order.reservation_ids)
            order.can_book_stock = bool(order.id and order.state in ("draft", "sent") and order._lines_to_book())

    def _lines_to_book(self):
        booked = self.reservation_ids.filtered(lambda r: r.booking_status == "booked").sale_order_line_id
        return self.order_line.filtered(lambda l: l.product_id.type == "product") - booked

    def action_book_stock(self):
        self.ensure_one()
        lines = self._lines_to_book()
        if not lines:
            raise UserError(_("All stockable products of this quotation are already booked."))
        self.env["mst.stock.reservation"].create([{
            "sale_order_line_id": line.id,
            "product_id": line.product_id.id,
            "product_uom_qty": line.product_uom_qty,
            "product_uom": line.product_uom.id,
        } for line in lines])._hold_stock()

    def action_view_bookings(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Stock Reservations"),
            "res_model": "mst.stock.reservation",
            "view_mode": "tree,form",
            "domain": [("sale_order_id", "=", self.id)],
            "context": {"create": False},
        }

    def action_confirm(self):
        # Free the booked stock before the delivery reserves, so the delivery can take it.
        self.reservation_ids.filtered(lambda r: r.booking_status == "booked")._utilize()
        for order in self.filtered(lambda o: o.booking_id.state == "booked"):
            order.booking_id._utilize(order.order_line)
        return super().action_confirm()

    def _action_cancel(self):
        self.reservation_ids.filtered(lambda r: r.booking_status == "booked")._release_all()
        return super()._action_cancel()
