from odoo import fields, models


class ProductCategory(models.Model):
    _inherit = "product.category"

    delivery_yarn = fields.Boolean(
        "Benang (Data Pengiriman)",
        help="Kiriman produk di kategori ini dihitung terpisah (kg / bal), tidak dijadikan yds.")
