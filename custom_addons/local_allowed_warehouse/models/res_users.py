from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    allowed_warehouse_ids = fields.Many2many(
        "stock.warehouse", string="Available Warehouses",
        help="User can access all warehouses, if left blank.")
