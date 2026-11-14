from odoo import fields, models, tools

from .sample_request import STATES


class SampleRequestReport(models.Model):
    """R-12: one row per requested product, with the processing time and the conversion to a Sales Order of the
    same customer and product within 90 days after the request was done."""

    _name = "mst.sample.request.report"
    _description = "Laporan Permintaan Sampel"
    _auto = False
    _order = "date_submit desc"

    request_id = fields.Many2one("mst.sample.request", "Nomor", readonly=True)
    date_submit = fields.Datetime("Tgl ajukan", readonly=True)
    date_done = fields.Datetime("Tgl selesai", readonly=True)
    user_id = fields.Many2one("res.users", "Sales peminta", readonly=True)
    team_id = fields.Many2one("crm.team", "Sales Team", readonly=True)
    partner_id = fields.Many2one("res.partner", "Customer", readonly=True)
    product_id = fields.Many2one("product.product", "Produk", readonly=True)
    sample_type = fields.Selection([("free", "Gratis"), ("paid", "Berbayar")], "Jenis sampel", readonly=True)
    state = fields.Selection(STATES, "Status", readonly=True)
    location_dest_id = fields.Many2one("stock.location", "Lokasi tujuan", readonly=True)
    company_id = fields.Many2one("res.company", readonly=True)
    qty = fields.Float("Qty diminta (y)", readonly=True)
    qty_cut = fields.Float("Qty potong (y)", readonly=True)
    process_days = fields.Float("Lama proses (hari)", readonly=True, group_operator="avg",
                                help="From Diajukan to Selesai.")
    converted = fields.Boolean("Jadi SO 90 hari", readonly=True)
    converted_count = fields.Integer("# Jadi SO 90 hari", readonly=True)

    def init(self):
        tools.drop_view_if_exists(self.env.cr, self._table)
        self.env.cr.execute("""
            CREATE VIEW %s AS (
                SELECT l.id, r.id AS request_id, r.date_submit, r.date_done, r.user_id, r.team_id, r.partner_id,
                       l.product_id, r.sample_type, r.state, r.location_dest_id, r.company_id, l.qty, l.qty_cut,
                       EXTRACT(EPOCH FROM r.date_done - r.date_submit) / 86400.0 AS process_days,
                       conv.found AS converted,
                       CASE WHEN conv.found THEN 1 ELSE 0 END AS converted_count
                  FROM mst_sample_request_line l
                  JOIN mst_sample_request r ON r.id = l.request_id
                  JOIN res_partner rp ON rp.id = r.partner_id
                 CROSS JOIN LATERAL (
                       SELECT r.date_done IS NOT NULL AND EXISTS (
                           SELECT 1
                             FROM sale_order_line sol
                             JOIN sale_order so ON so.id = sol.order_id
                             JOIN res_partner sp ON sp.id = so.partner_id
                            WHERE sol.product_id = l.product_id
                              AND sp.commercial_partner_id = rp.commercial_partner_id
                              AND so.state IN ('sale', 'done')
                              AND so.id IS DISTINCT FROM r.sale_order_id
                              AND so.date_order >= r.date_done
                              AND so.date_order < r.date_done + INTERVAL '90 days'
                       ) AS found
                 ) conv
            )
        """ % self._table)
