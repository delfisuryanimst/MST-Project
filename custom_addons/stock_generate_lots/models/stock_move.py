from math import ceil

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round

MAX_GENERATED_LOTS = 2000


class StockMove(models.Model):
    _inherit = "stock.move"

    @api.model
    def action_generate_lot_line_vals_by_quantity(self, context, first_lot, qty_per_lot, qty_received):
        """Split ``qty_received`` into lots of ``qty_per_lot``; the last lot takes the remainder."""
        first_lot = (first_lot or "").strip()
        if not first_lot:
            raise UserError(_("Isi First Lot Number."))
        if qty_per_lot <= 0 or qty_received <= 0:
            raise UserError(_("Quantity per Lot dan Quantity Received harus lebih besar dari 0."))

        product = self.env["product.product"].browse(context.get("default_product_id"))
        rounding = product.uom_id.rounding or 0.01
        lot_count = ceil(float_round(qty_received / qty_per_lot, precision_digits=6))
        if lot_count > MAX_GENERATED_LOTS:
            raise UserError(_(
                "Terlalu banyak lot (%(count)s). Maksimal %(max)s lot sekali generate; "
                "perbesar Quantity per Lot.", count=lot_count, max=MAX_GENERATED_LOTS))

        quantities = []
        remaining = qty_received
        while float_compare(remaining, 0.0, precision_rounding=rounding) > 0:
            quantity = min(qty_per_lot, remaining)
            quantities.append(quantity)
            remaining = float_round(remaining - quantity, precision_rounding=rounding)

        vals_list = self.action_generate_lot_line_vals(context, "serial", first_lot, len(quantities), "")
        for vals, quantity in zip(vals_list, quantities):
            vals["quantity"] = quantity
        return vals_list
