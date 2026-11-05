from math import ceil

from odoo import _, api, models
from odoo.exceptions import UserError
from odoo.tools import float_compare, float_round

MAX_GENERATED_LOTS = 2000
# Format seen on real receipts: P03949.03048008.01.0001.16404
#   PO name . product internal reference . PO line . lot running no. . lot quantity x 100
LINE_DIGITS = 2  # .01  (position of the line in the PO)
SEQUENCE_DIGITS = 4  # .0001 (running number of the lot, starts at 0001)
QTY_DIGITS = 5  # .16404 (164.04 -> quantity x 100, no decimal point)


class StockMove(models.Model):
    _inherit = "stock.move"

    def _lot_po_segment(self):
        """Name of the PO the receipt comes from (e.g. 'P03949')."""
        self.ensure_one()
        purchase_line = self.purchase_line_id if "purchase_line_id" in self._fields else False
        name = (purchase_line.order_id.name if purchase_line else self.picking_id.origin) or ""
        if not name.strip():
            raise UserError(_(
                "Nomor lot otomatis butuh Receipt yang berasal dari PO. Isi First Lot Number secara manual."))
        return name.strip()

    def _lot_line_segment(self):
        self.ensure_one()
        purchase_line = self.purchase_line_id if "purchase_line_id" in self._fields else False
        if not purchase_line:
            return "01".zfill(LINE_DIGITS)
        lines = purchase_line.order_id.order_line.filtered(lambda line: not line.display_type)
        return str((lines.ids.index(purchase_line.id) + 1) if purchase_line.id in lines.ids else 1).zfill(LINE_DIGITS)

    @staticmethod
    def _lot_qty_segment(quantity):
        """164.04 -> '16404', 76.55 -> '07655', 100 -> '10000'."""
        return str(int(round(quantity * 100))).zfill(QTY_DIGITS)

    def _generate_lot_names_auto(self, product, quantities):
        """<PO>.<product code>.<line>.<running no.>.<lot quantity x 100>, e.g. P03949.03048008.01.0001.16404"""
        self.ensure_one()
        if not product.default_code:
            raise UserError(_("Produk %s belum punya Internal Reference.", product.display_name))
        prefix = "%s.%s.%s" % (self._lot_po_segment(), product.default_code, self._lot_line_segment())
        return [
            "%s.%s.%s" % (prefix, str(index).zfill(SEQUENCE_DIGITS), self._lot_qty_segment(quantity))
            for index, quantity in enumerate(quantities, start=1)
        ]

    @api.model
    def action_generate_lot_line_vals_by_quantity(self, context, first_lot, qty_per_lot, qty_received):
        """Split ``qty_received`` into lots of ``qty_per_lot``; the last lot takes the remainder.

        An empty ``first_lot`` names the lots automatically (see ``_generate_lot_names_auto``).
        """
        first_lot = (first_lot or "").strip()
        move = self.browse(context.get("move_id")).exists()
        if not first_lot and not move:
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

        if first_lot:
            vals_list = self.action_generate_lot_line_vals(context, "serial", first_lot, len(quantities), "")
        else:
            names = move._generate_lot_names_auto(product, quantities)
            vals_list = self.action_generate_lot_line_vals(context, "import", "", len(names), "\n".join(names))
        for vals, quantity in zip(vals_list, quantities):
            vals["quantity"] = quantity
        return vals_list
