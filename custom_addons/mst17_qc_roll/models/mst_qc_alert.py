from odoo import _, api, fields, models

RESULT_TYPES = [
    ("pass", "Pass"),
    ("data", "Lot Warna"),
    ("qty", "Qty Correction"),
    ("downgrade", "Downgrade"),
    ("scrap", "Scrap"),
    ("split", "Split"),
    ("mixed", "Mixed"),
]


class MstQcAlert(models.Model):
    """Record of a roll that did not pass fully. Made by Validate only; there is nothing to work on."""

    _name = "mst.qc.alert"
    _description = "QC Roll Alert"
    _order = "id desc"

    name = fields.Char(default=lambda self: _("New"), readonly=True, copy=False)
    inspection_id = fields.Many2one("mst.qc.inspection", readonly=True, index=True)
    lot_id = fields.Many2one("stock.lot", string="Roll", readonly=True)
    product_id = fields.Many2one("product.product", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Vendor", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="QCP", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True, default=lambda self: self.env.company)
    result_type = fields.Selection(RESULT_TYPES, readonly=True)
    qty_before = fields.Float(digits=(16, 2), readonly=True)
    qty_result = fields.Float(digits=(16, 2), readonly=True)
    qty_down = fields.Float(digits=(16, 2), readonly=True)
    qty_scrap = fields.Float(digits=(16, 2), readonly=True)
    qty_diff = fields.Float(string="Qty Difference", digits=(16, 2), readonly=True)
    reason = fields.Text(readonly=True)
    conversion_ids = fields.Many2many(
        "qc.conversion", "mst_qc_alert_conversion_rel", "alert_id", "conversion_id",
        string="Simplified MOs", readonly=True)
    scrap_ids = fields.Many2many(
        "stock.scrap", "mst_qc_alert_scrap_rel", "alert_id", "scrap_id", string="Scraps", readonly=True)
    state = fields.Selection([("done", "Done"), ("cancelled", "Cancelled")], default="done", readonly=True)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("mst.qc.alert") or _("New")
        return super().create(vals_list)
