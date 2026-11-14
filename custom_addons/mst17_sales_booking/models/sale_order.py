from odoo import Command, _, fields, models
from odoo.exceptions import UserError


class SaleOrder(models.Model):
    _inherit = "sale.order"

    booking_id = fields.Many2one(
        "mst.sale.booking", string="Booking Reference", copy=False, tracking=True,
        domain="[('state', '=', 'booked'), ('partner_id', '=', partner_id), ('company_id', '=', company_id)]")

    def action_book_stock(self):
        self.ensure_one()
        lines = self.order_line.filtered(lambda l: l.product_id.type == "product")
        if not lines:
            raise UserError(_("There are no stockable products to book."))
        self.booking_id = self.env["mst.sale.booking"].create({
            "partner_id": self.partner_id.id,
            "user_id": self.user_id.id,
            "company_id": self.company_id.id,
            "warehouse_id": self.warehouse_id.id,
            "line_ids": [Command.create({
                "product_id": line.product_id.id,
                "product_uom_qty": line.product_uom_qty,
                "product_uom": line.product_uom.id,
            }) for line in lines],
        })
        self.booking_id.action_confirm()

    def action_confirm(self):
        # Free the booked stock before the delivery reserves, so the delivery can take it.
        for order in self.filtered(lambda o: o.booking_id.state == "booked"):
            order.booking_id._utilize(order.order_line)
        return super().action_confirm()
