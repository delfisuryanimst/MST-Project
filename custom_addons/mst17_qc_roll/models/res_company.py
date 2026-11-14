from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    mst_qc_qty_tolerance = fields.Float(
        string="QC Qty Tolerance (%)", digits=(16, 2),
        help="A qty difference above this percent of the roll needs a reason and gets a QC Roll Alert. "
             "The value is decided by Finance.")
    mst_roll_id_start = fields.Date(
        string="ID Roll Start Date",
        help="From this date, a new lot on a receipt is named with the ID Roll sequence (R26-000123) "
             "when no lot name is given. Empty: never.")
    mst_qc_location_id = fields.Many2one(
        "stock.location", string="QC Location", domain=[("usage", "=", "internal")],
        help="Where rolls wait for QC (QC0500). A transfer into it is logged as 'Sent to QC' in the roll "
             "history.")
