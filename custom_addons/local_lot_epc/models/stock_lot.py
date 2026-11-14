from odoo import fields, models


class StockLot(models.Model):
    _inherit = "stock.lot"

    x_epc = fields.Char("EPC", index=True, copy=False)
