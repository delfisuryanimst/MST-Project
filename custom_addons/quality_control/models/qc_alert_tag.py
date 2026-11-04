from odoo import fields, models


class QcAlertTag(models.Model):
    _name = "qc.alert.tag"
    _description = "Quality Alert Tag"
    _order = "name"

    name = fields.Char(string="Title", required=True, translate=True)
    color = fields.Integer()

    _sql_constraints = [("name_uniq", "unique(name)", "Nama tag sudah ada!")]
