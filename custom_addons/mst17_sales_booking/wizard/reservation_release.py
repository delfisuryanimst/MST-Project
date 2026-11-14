from odoo import fields, models


class StockReservationRelease(models.TransientModel):
    _name = "mst.stock.reservation.release"
    _description = "Release Stock Reservation"

    reservation_id = fields.Many2one("mst.stock.reservation", required=True, ondelete="cascade")
    remaining_qty = fields.Float(related="reservation_id.remaining_qty")
    product_uom = fields.Many2one(related="reservation_id.product_uom")
    qty = fields.Float(string="Qty to Release", required=True, digits="Product Unit of Measure")

    def action_release(self):
        self.reservation_id._release(self.qty)
