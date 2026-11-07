from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mst_qc_qty_tolerance = fields.Float(
        string="QC Qty Tolerance (%)", digits=(16, 2),
        help="A qty difference above this percent of the roll needs a reason and gets a QC Roll Alert. "
             "The value is decided by Finance.")
