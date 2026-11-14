from odoo import fields, models

ROLL_EVENTS = [
    ("masuk", "Received"),
    ("kirim_qc", "Sent to QC"),
    ("pindah", "Moved"),
    ("lot_warna", "Lot Warna"),
    ("qty", "Qty Correction"),
    ("pecah", "Split"),
    ("ganti_produk", "Product Change"),
    ("turun_grade", "Downgrade"),
    ("scrap", "Scrap"),
    ("epc", "EPC"),
    ("label", "Label"),
    ("kirim_customer", "Sent to Customer"),
]


class MstRollHistory(models.Model):
    """Everything that happened to one roll (lot), oldest to newest."""

    _name = "mst.roll.history"
    _description = "Roll History"
    _order = "date desc, id desc"

    lot_id = fields.Many2one("stock.lot", string="Roll", required=True, index=True, ondelete="cascade")
    product_id = fields.Many2one(related="lot_id.product_id", store=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    user_id = fields.Many2one("res.users", default=lambda self: self.env.user, required=True)
    event = fields.Selection(ROLL_EVENTS, required=True)
    old_value = fields.Char()
    new_value = fields.Char()
    res_model = fields.Char(string="Document Model")
    res_id = fields.Many2oneReference(string="Document ID", model_field="res_model")
    document_name = fields.Char(compute="_compute_document_name")
    note = fields.Text()

    def _compute_document_name(self):
        for line in self:
            document = line.res_model and line.res_id and self.env[line.res_model].sudo().browse(line.res_id)
            line.document_name = document.exists().display_name if document else False

    def action_open_document(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": self.res_model,
            "res_id": self.res_id,
            "view_mode": "form",
        }
