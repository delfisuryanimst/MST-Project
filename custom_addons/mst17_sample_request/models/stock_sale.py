from odoo import fields, models


class StockPicking(models.Model):
    _inherit = "stock.picking"

    sample_request_id = fields.Many2one("mst.sample.request", "Permintaan Sampel", readonly=True, index="btree_not_null",
                                        copy=False)

    def _action_done(self):
        result = super()._action_done()
        self.sample_request_id.sudo()._check_done()
        return result


class SaleOrder(models.Model):
    _inherit = "sale.order"

    def action_confirm(self):
        result = super().action_confirm()
        self.env["mst.sample.request"].sudo().search([("sale_order_id", "in", self.ids)])._check_done()
        return result
