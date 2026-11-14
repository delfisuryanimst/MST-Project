from odoo import fields, models


class QcConversion(models.Model):
    _inherit = "qc.conversion"

    conversion_type = fields.Selection(
        selection_add=[("split", "Split"), ("product_change", "Product Change")],
        ondelete={"split": "set default", "product_change": "set default"})
