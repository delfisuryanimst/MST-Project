from odoo import fields, models


class StockLocation(models.Model):
    _inherit = "stock.location"

    order_calc_role = fields.Selection(
        [("qc", "QC"), ("excluded", "Tidak dijual")], string="Order Calculator Role",
        help="Hanya untuk sub-lokasi di dalam Stock yang bukan stok jual (mis. QC0500, SAM0500). "
             "Stock Location gudang otomatis rak, Input Location otomatis QC.")


class ProductCategory(models.Model):
    _inherit = "product.category"

    order_calc_fresh = fields.Boolean(string="Jalur Fresh (Order Kalkulator)")
