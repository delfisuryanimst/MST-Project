from collections import defaultdict

from markupsafe import Markup, escape

from odoo import _, api, fields, models


class SampleRequestProcess(models.TransientModel):
    """Confirmation of Proses: shows the documents Odoo will make (mockup board 2)."""

    _name = "mst.sample.request.process"
    _description = "Proses Permintaan Sampel"

    request_id = fields.Many2one("mst.sample.request", required=True, ondelete="cascade")
    preview = fields.Html(compute="_compute_preview", sanitize=False)

    @api.depends("request_id")
    def _compute_preview(self):
        for wizard in self:
            wizard.preview = wizard._preview_html(wizard.request_id)

    @api.model
    def _preview_html(self, request):
        row = Markup("<tr><td>%s</td><td>%s</td><td class='text-end'>%s</td></tr>")
        if request.sample_type == "free":
            qty = defaultdict(float)
            for line in request.line_ids:
                qty[line.location_src_id.display_name or _("(lokasi asal belum dipilih)")] += line.qty_cut
            head = _("Gratis → dibuat <b>Internal Transfer</b>")
            columns = (_("Dari"), _("Ke"), _("Qty"))
            rows = [row % (source, request.location_dest_id.display_name, total) for source, total in qty.items()]
            foot = _("Partner = %(partner)s · Source Document = %(name)s · Responsible = %(user)s. "
                     "Lot dipilih saat potong (Detailed Operations).",
                     partner=request.partner_id.display_name, name=request.name, user=self.env.user.name)
        else:
            head = _("Berbayar → dibuat <b>Quotation</b>")
            columns = (_("Produk"), _("Harga/y"), _("Qty"))
            rows = [row % (line.product_id.display_name, line.price_unit, line.qty_cut) for line in request.line_ids]
            foot = _("Customer = %(partner)s · Salesperson = %(user)s · Referensi = %(name)s.",
                     partner=request.partner_id.display_name, user=request.user_id.name, name=request.name)
        header = Markup("").join(Markup("<th>%s</th>") % column for column in columns)
        return (Markup("<p>%s</p><table class='table table-sm'><thead><tr>%s</tr></thead><tbody>%s</tbody></table>"
                       "<p class='text-muted small'>%s</p>")
                % (Markup(head), header, Markup("").join(rows), escape(foot)))

    def action_confirm(self):
        self.request_id._process()
        return {"type": "ir.actions.act_window_close"}


class SampleRequestReject(models.TransientModel):
    _name = "mst.sample.request.reject"
    _description = "Tolak Permintaan Sampel"

    request_id = fields.Many2one("mst.sample.request", required=True, ondelete="cascade")
    reason = fields.Text("Alasan", required=True)

    def action_confirm(self):
        self.request_id._reject(self.reason)
        return {"type": "ir.actions.act_window_close"}
