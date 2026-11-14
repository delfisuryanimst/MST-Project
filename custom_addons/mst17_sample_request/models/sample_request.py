from collections import defaultdict

from odoo import Command, _, api, fields, models
from odoo.exceptions import AccessError, UserError

GROUP_ADMIN_SALES = "mst17_sample_request.group_sample_admin_sales"
GROUP_CUTTER = "mst17_sample_request.group_sample_cutter"
STATES = [
    ("draft", "Draft"),
    ("submitted", "Diajukan"),
    ("processing", "Diproses"),
    ("done", "Selesai"),
    ("rejected", "Ditolak"),
]


class SampleRequest(models.Model):
    _name = "mst.sample.request"
    _description = "Permintaan Sampel"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char("Nomor", default=lambda self: _("New"), readonly=True, copy=False)
    user_id = fields.Many2one(
        "res.users", "Sales peminta", required=True, index=True, tracking=True,
        default=lambda self: self.env.user, domain=[("share", "=", False)])
    team_id = fields.Many2one("crm.team", "Sales Team", compute="_compute_team_id", store=True, readonly=False)
    partner_id = fields.Many2one("res.partner", "Customer", required=True, index=True, tracking=True)
    sample_type = fields.Selection(
        [("free", "Gratis"), ("paid", "Berbayar")], "Jenis sampel", required=True, default="free", tracking=True)
    purpose = fields.Selection([
        ("development", "Development"),
        ("color", "Approval warna"),
        ("reorder", "Order ulang"),
        ("exhibition", "Pameran"),
        ("other", "Lainnya"),
    ], "Tujuan sampel")
    date_need = fields.Date("Dibutuhkan tanggal")
    location_dest_id = fields.Many2one(
        "stock.location", "Lokasi tujuan", compute="_compute_location_dest_id", store=True, readonly=False,
        tracking=True)
    note = fields.Text("Catatan")
    entry_uid = fields.Many2one("res.users", "Diinput oleh", default=lambda self: self.env.user, readonly=True)
    state = fields.Selection(STATES, "Status", default="draft", required=True, readonly=True, tracking=True,
                             copy=False, index=True)
    reject_reason = fields.Text("Alasan ditolak", readonly=True, copy=False)
    picking_ids = fields.One2many("stock.picking", "sample_request_id", "Internal Transfer", readonly=True)
    sale_order_id = fields.Many2one("sale.order", "Quotation", readonly=True, copy=False)
    document_names = fields.Char("Dokumen hasil", compute="_compute_document_names")
    picking_count = fields.Integer("Transfer", compute="_compute_document_names")
    has_quotation = fields.Boolean(compute="_compute_document_names")
    date_submit = fields.Datetime("Tgl ajukan", readonly=True, copy=False)
    date_done = fields.Datetime("Tgl selesai", readonly=True, copy=False)
    line_ids = fields.One2many("mst.sample.request.line", "request_id", "Produk", copy=True)
    product_count = fields.Integer("Jumlah produk", compute="_compute_totals", store=True)
    qty_total = fields.Float("Qty (y)", compute="_compute_totals", store=True, digits="Product Unit of Measure")
    amount_total = fields.Float("Total (sebelum pajak)", compute="_compute_totals", store=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    is_admin_sales = fields.Boolean(compute="_compute_is_admin_sales")

    @api.depends("user_id")
    def _compute_team_id(self):
        for request in self:
            request.team_id = request.user_id.sale_team_id

    @api.depends("team_id", "company_id")
    def _compute_location_dest_id(self):
        for request in self:
            request.location_dest_id = request.team_id.sample_location_id or request.company_id.sample_location_id

    @api.depends("picking_ids", "sale_order_id")
    def _compute_document_names(self):
        # Read with sudo: a cutting admin has no Sales rights, a sales user may have no Inventory rights.
        for request in self:
            documents = request.sudo()
            names = documents.picking_ids.mapped("name") + documents.sale_order_id.mapped("name")
            request.document_names = ", ".join(names) or False
            request.picking_count = len(documents.picking_ids)
            request.has_quotation = bool(documents.sale_order_id)

    def action_view_pickings(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("stock.action_picking_tree_all")
        if len(self.picking_ids) == 1:
            action.update({"view_mode": "form", "views": [(False, "form")], "res_id": self.picking_ids.id})
        else:
            action["domain"] = [("sample_request_id", "=", self.id)]
        action["context"] = {"create": False}
        return action

    def action_view_quotation(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "view_mode": "form",
            "res_id": self.sale_order_id.id,
        }

    @api.depends("line_ids.qty", "line_ids.price_unit")
    def _compute_totals(self):
        for request in self:
            lines = request.line_ids
            request.product_count = len(lines)
            request.qty_total = sum(lines.mapped("qty"))
            request.amount_total = sum(line.qty * line.price_unit for line in lines)

    @api.depends_context("uid")
    def _compute_is_admin_sales(self):
        self.is_admin_sales = self.env.user.has_group(GROUP_ADMIN_SALES)

    # ------------------------------------------------------------------ requester

    def _check_requester(self, user_id):
        """R-04: only Admin Sales may file a request for another user."""
        if user_id and user_id != self.env.uid and not self.env.su and not self.env.user.has_group(GROUP_ADMIN_SALES):
            raise AccessError(_("Hanya Admin Sales yang dapat mengisi permintaan atas nama peminta lain."))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._check_requester(vals.get("user_id"))
            vals["entry_uid"] = self.env.uid
            if vals.get("name", _("Baru")) == _("Baru"):
                vals["name"] = self.env["ir.sequence"].next_by_code("mst.sample.request") or _("Baru")
        return super().create(vals_list)

    def write(self, vals):
        if "user_id" in vals:
            self._check_requester(vals["user_id"])
        return super().write(vals)

    def unlink(self):
        if any(request.state != "draft" for request in self):
            raise UserError(_("Hanya permintaan Draft yang dapat dihapus."))
        return super().unlink()

    # ------------------------------------------------------------------ flow

    def action_submit(self):
        for request in self:
            if request.state != "draft":
                raise UserError(_("%s sudah diajukan.", request.name))
            if not request.line_ids:
                raise UserError(_("%s: tambahkan minimal satu produk.", request.name))
            if any(line.qty <= 0 for line in request.line_ids):
                raise UserError(_("%s: qty setiap produk harus lebih dari 0.", request.name))
            if request.sample_type == "paid" and any(line.price_unit <= 0 for line in request.line_ids):
                raise UserError(_("%s: sampel berbayar wajib diisi Harga/y untuk setiap produk.", request.name))
            if not request.location_dest_id:
                raise UserError(_("%s: lokasi tujuan belum diatur (Sales Team atau pengaturan perusahaan).",
                                  request.name))
        self.write({"state": "submitted", "date_submit": fields.Datetime.now()})
        for line in self.line_ids:
            line.qty_cut = line.qty_cut or line.qty
        self.line_ids._suggest_location()
        self._notify_cutters()
        return True

    def _notify_cutters(self):
        cutters = self.env.ref(GROUP_CUTTER).sudo().users
        for request in self.sudo():
            for user in cutters:
                request.activity_schedule(
                    "mail.mail_activity_data_todo", user_id=user.id, date_deadline=request.date_need,
                    summary=_("Proses permintaan sampel %s", request.name))

    def _check_cutter(self):
        if not self.env.su and not self.env.user.has_group(GROUP_CUTTER):
            raise AccessError(_("Hanya Admin Pemotong yang dapat memproses atau menolak permintaan sampel."))

    def _check_submitted(self):
        for request in self:
            if request.state != "submitted":
                raise UserError(_("%s tidak berstatus Diajukan.", request.name))

    def action_open_process(self):
        self.ensure_one()
        self._check_cutter()
        self._check_submitted()
        return self._open_wizard("mst.sample.request.process", _("Proses permintaan · konfirmasi"))

    def action_open_reject(self):
        self.ensure_one()
        self._check_cutter()
        self._check_submitted()
        return self._open_wizard("mst.sample.request.reject", _("Tolak permintaan"))

    def _open_wizard(self, model, title):
        return {
            "type": "ir.actions.act_window",
            "name": title,
            "res_model": model,
            "view_mode": "form",
            "target": "new",
            "context": {"default_request_id": self.id},
        }

    def _process(self):
        """R-08 / R-09: make the documents with server rights and link them to the request."""
        self.ensure_one()
        self._check_cutter()
        self._check_submitted()
        lines = self.line_ids
        if any(line.qty_cut <= 0 for line in lines):
            raise UserError(_("Isi Qty potong untuk setiap produk."))
        if self.sample_type == "free":
            if any(not line.location_src_id for line in lines):
                raise UserError(_("Pilih lokasi asal potong untuk setiap produk."))
            self._create_pickings()
        else:
            self._create_quotation()
        self.state = "processing"
        self.sudo().activity_feedback(["mail.mail_activity_data_todo"])

    def _create_pickings(self):
        """One Internal Transfer per source location, one move per line."""
        company = self.company_id
        picking_type = company.sample_picking_type_id
        if not picking_type:
            raise UserError(_("Jenis Transfer Sampel belum diatur di pengaturan perusahaan (tab Permintaan Sampel)."))
        by_source = defaultdict(lambda: self.env["mst.sample.request.line"])
        for line in self.line_ids:
            by_source[line.location_src_id] |= line
        dest = self.location_dest_id
        for source, lines in by_source.items():
            picking = self.env["stock.picking"].sudo().create({
                "picking_type_id": picking_type.id,
                "location_id": source.id,
                "location_dest_id": dest.id,
                "partner_id": self.partner_id.id,
                "origin": self.name,
                "user_id": self.env.uid,
                "company_id": company.id,
                "sample_request_id": self.id,
                "move_ids": [Command.create({
                    "name": line.product_id.display_name,
                    "product_id": line.product_id.id,
                    "product_uom": line.product_id.uom_id.id,
                    "product_uom_qty": line.qty_cut,
                    "location_id": source.id,
                    "location_dest_id": dest.id,
                    "company_id": company.id,
                }) for line in lines],
            })
            for line, move in zip(lines, picking.move_ids.sorted("id")):
                line.move_id = move
            picking.action_confirm()

    def _create_quotation(self):
        self.sale_order_id = self.env["sale.order"].sudo().create({
            "partner_id": self.partner_id.id,
            "user_id": self.user_id.id,
            "team_id": self.team_id.id,
            "origin": self.name,
            "client_order_ref": self.name,
            "company_id": self.company_id.id,
            "order_line": [Command.create({
                "product_id": line.product_id.id,
                "product_uom_qty": line.qty_cut,
                "price_unit": line.price_unit,
            }) for line in self.line_ids],
        })

    def _reject(self, reason):
        self.ensure_one()
        self._check_cutter()
        self._check_submitted()
        if not (reason or "").strip():
            raise UserError(_("Alasan wajib diisi."))
        self.write({"state": "rejected", "reject_reason": reason})
        self.sudo().activity_feedback(["mail.mail_activity_data_todo"])

    def _check_done(self):
        """Done when every transfer is done (free) or the quotation is confirmed (paid)."""
        for request in self.filtered(lambda r: r.state == "processing"):
            if request.sample_type == "free":
                pickings = request.picking_ids.filtered(lambda p: p.state != "cancel")
                done = bool(pickings) and all(p.state == "done" for p in pickings)
            else:
                done = request.sale_order_id.state in ("sale", "done")
            if done:
                request.write({"state": "done", "date_done": fields.Datetime.now()})


class SampleRequestLine(models.Model):
    _name = "mst.sample.request.line"
    _description = "Baris Permintaan Sampel"
    _order = "request_id, id"

    request_id = fields.Many2one("mst.sample.request", required=True, ondelete="cascade", index=True)
    state = fields.Selection(related="request_id.state")
    sample_type = fields.Selection(related="request_id.sample_type")
    company_id = fields.Many2one(related="request_id.company_id", store=True)
    product_id = fields.Many2one(
        "product.product", "Produk", required=True, domain=[("type", "in", ("product", "consu"))])
    uom_id = fields.Many2one(related="product_id.uom_id", string="Satuan")
    qty = fields.Float("Diminta (y)", required=True, default=1.0, digits="Product Unit of Measure")
    price_unit = fields.Float("Harga/y", digits="Product Price")
    location_src_id = fields.Many2one(
        "stock.location", "Lokasi asal potong", domain=[("usage", "=", "internal")])
    qty_free = fields.Float(
        "Stok lokasi", compute="_compute_qty_free", digits="Product Unit of Measure",
        help="Free stock (on hand minus reserved) of the product in the source location.")
    qty_cut = fields.Float("Qty potong", digits="Product Unit of Measure")
    move_id = fields.Many2one("stock.move", "Move", readonly=True, copy=False)

    def _free_qty(self, location):
        self.ensure_one()
        return self.env["stock.quant"].sudo()._get_available_quantity(self.product_id, location)

    @api.depends("product_id", "location_src_id")
    def _compute_qty_free(self):
        for line in self:
            line.qty_free = line._free_qty(line.location_src_id) if line.product_id and line.location_src_id else 0.0

    def _suggest_location(self):
        """R-07: the sample source location with the most free stock; nothing when no source has stock."""
        lines = self.filtered(lambda line: not line.location_src_id)
        if not lines:
            return
        sources = self.env["stock.location"].sudo().search([("sample_source", "=", True), ("usage", "=", "internal")])
        for line in lines:
            stock = {location: line._free_qty(location) for location in sources}
            best = max(stock, key=stock.get, default=False)
            if best and stock[best] > 0:
                line.location_src_id = best
