from odoo import fields, models


class SkuCategories(models.Model):
    _name = "sku.categories"
    _description = "SKU Categories"

    name = fields.Char("Category", required=True)
    value_ids = fields.One2many("sku.values", "category_id", "Values")


class SkuValues(models.Model):
    _name = "sku.values"
    _description = "SKU Values"

    name = fields.Char("Value", required=True)
    category_id = fields.Many2one("sku.categories", "Category")


class ProductTemplate(models.Model):
    _inherit = "product.template"

    sku_gsm_finish_id = fields.Many2one("sku.values", "GSM Finish")
    sku_width_finish_id = fields.Many2one("sku.values", "Width Finish")
    sku_pemakaian_id = fields.Many2one("sku.values", "Pemakaian")
