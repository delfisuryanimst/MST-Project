from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_is_zero


class StockHoldMixin(models.AbstractModel):
    """Holds free stock for a customer with a move that is reserved but never done."""
    _name = "mst.stock.hold.mixin"
    _description = "Stock Hold"

    product_id = fields.Many2one("product.product", required=True, check_company=True,
                                 domain="[('type', '=', 'product')]")
    product_uom_qty = fields.Float(string="Booked Qty", required=True, default=1.0,
                                   digits="Product Unit of Measure")
    product_uom = fields.Many2one("uom.uom", string="UoM", required=True, compute="_compute_product_uom",
                                  store=True, readonly=False, precompute=True,
                                  domain="[('category_id', '=', product_uom_category_id)]")
    product_uom_category_id = fields.Many2one(related="product_id.uom_id.category_id")
    used_qty = fields.Float(string="Used Qty", readonly=True, copy=False, digits="Product Unit of Measure")
    released_qty = fields.Float(string="Released Qty", readonly=True, copy=False, digits="Product Unit of Measure")
    remaining_qty = fields.Float(string="Remaining Qty", compute="_compute_remaining_qty",
                                 digits="Product Unit of Measure")
    free_qty = fields.Float(string="Available Quantity", compute="_compute_free_qty",
                            digits="Product Unit of Measure")
    move_id = fields.Many2one("stock.move", string="Reservation Move", readonly=True, copy=False)

    @api.depends("product_id")
    def _compute_product_uom(self):
        for rec in self:
            rec.product_uom = rec.product_id.uom_id

    @api.depends("product_uom_qty", "used_qty", "released_qty")
    def _compute_remaining_qty(self):
        for rec in self:
            rec.remaining_qty = rec.product_uom_qty - rec.used_qty - rec.released_qty

    @api.depends("product_id")
    def _compute_free_qty(self):
        for rec in self:
            product = rec.product_id.with_context(location=rec._hold_location().id)
            rec.free_qty = product.uom_id._compute_quantity(product.free_qty, rec.product_uom or product.uom_id)

    def _hold_location(self):
        raise NotImplementedError

    def _hold_stock(self):
        dest = self.env.ref("mst17_sales_booking.stock_location_booking")
        moves = self.env["stock.move"].sudo().create([{
            "name": rec.display_name,
            "origin": rec.display_name,
            "product_id": rec.product_id.id,
            "product_uom_qty": rec.product_uom_qty,
            "product_uom": rec.product_uom.id,
            "location_id": rec._hold_location().id,
            "location_dest_id": dest.id,
            "company_id": rec.company_id.id,
        } for rec in self])
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
        for rec, move in zip(self, moves):
            rec.move_id = move

    def _resize_hold(self):
        """Make the hold match the remaining quantity; cancel it when nothing remains."""
        for rec in self:
            move = rec.move_id.sudo()
            if move.state in ("done", "cancel"):
                continue
            if float_is_zero(rec.remaining_qty, precision_rounding=rec.product_uom.rounding):
                move._action_cancel()
            else:
                move._do_unreserve()
                move.product_uom_qty = rec.remaining_qty
                move._action_assign()
