from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context


class QcConversion(models.Model):
    """Stock Conversion: mengubah stok produk asal (Grade A) menjadi produk hasil (B Grade)."""

    _name = "qc.conversion"
    _description = "Stock Conversion"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Name", required=True, copy=False, default=lambda self: _("Downgrade"), tracking=True)
    date = fields.Datetime(string="Date", default=fields.Datetime.now, required=True)
    company_id = fields.Many2one("res.company", string="Company", required=True, default=lambda self: self.env.company)
    origin = fields.Char(string="Source Document")
    alert_id = fields.Many2one("qc.alert", string="Quality Alert", index=True, copy=False)
    state = fields.Selection(
        [("draft", "Draft"), ("done", "Validate"), ("cancel", "Cancelled")],
        string="Status", default="draft", required=True, copy=False, tracking=True)

    consume_line_ids = fields.One2many(
        "qc.conversion.line", "conversion_id", string="To be Consumed Item",
        domain=[("line_type", "=", "consume")], context={"default_line_type": "consume"})
    produce_line_ids = fields.One2many(
        "qc.conversion.line", "conversion_id", string="To be Produced Item",
        domain=[("line_type", "=", "produce")], context={"default_line_type": "produce"})
    consume_total = fields.Float(compute="_compute_totals", digits=(16, 2))
    produce_total = fields.Float(compute="_compute_totals", digits=(16, 2))
    move_ids = fields.One2many("stock.move", "qc_conversion_id", string="Inventory Adjustments", readonly=True)

    @api.depends("consume_line_ids.qty", "produce_line_ids.qty")
    def _compute_totals(self):
        for conversion in self:
            conversion.consume_total = sum(conversion.consume_line_ids.mapped("qty"))
            conversion.produce_total = sum(conversion.produce_line_ids.mapped("qty"))

    def action_generate_lot(self):
        """Buat lot untuk baris hasil yang bertracking tapi belum punya lot, memakai nomor lot barang asal."""
        self.ensure_one()
        self = self.with_context(clean_context(self.env.context))
        source_lot = self.consume_line_ids.lot_id[:1]
        for line in self.produce_line_ids.filtered(lambda l: l.tracking != "none" and not l.lot_id):
            if not source_lot:
                raise UserError(_("Isi Lot pada To be Consumed Item terlebih dahulu."))
            line.lot_id = self.env["stock.lot"].search([
                ("name", "=", source_lot.name),
                ("product_id", "=", line.product_id.id),
                ("company_id", "=", self.company_id.id),
            ], limit=1) or self.env["stock.lot"].create({
                "name": source_lot.name,
                "product_id": line.product_id.id,
                "company_id": self.company_id.id,
            })

    def _check_ready(self):
        self.ensure_one()
        if not self.consume_line_ids or not self.produce_line_ids:
            raise UserError(_("Isi To be Consumed Item dan To be Produced Item terlebih dahulu."))
        for line in self.consume_line_ids | self.produce_line_ids:
            if line.qty <= 0:
                raise UserError(_("Qty harus lebih besar dari 0: %s", line.product_id.display_name))
            if line.tracking != "none" and not line.lot_id:
                raise UserError(_("Lot wajib diisi untuk: %s", line.product_id.display_name))
        if round(self.consume_total - self.produce_total, 2) != 0:
            raise UserError(_(
                "Total To be Consumed (%(consumed).2f) harus sama dengan total To be Produced (%(produced).2f).",
                consumed=self.consume_total, produced=self.produce_total))

    def action_validate(self):
        self = self.with_context(clean_context(self.env.context))
        for conversion in self:
            if conversion.state != "draft":
                continue
            conversion._check_ready()
            moves = self.env["stock.move"]
            for line in conversion.consume_line_ids:
                moves |= line._create_move()
            for line in conversion.produce_line_ids:
                moves |= line._create_move()
            moves._action_done()
            conversion.write({"state": "done"})
        return True

    def action_open_alert(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_alert_action")
        action.update({"views": [(False, "form")], "res_id": self.alert_id.id})
        return action

    def action_cancel(self):
        if self.filtered(lambda c: c.state == "done"):
            raise UserError(_("Konversi yang sudah divalidasi tidak dapat dibatalkan."))
        self.write({"state": "cancel"})

    def action_draft(self):
        self.filtered(lambda c: c.state == "cancel").write({"state": "draft"})

    def unlink(self):
        if self.filtered(lambda c: c.state == "done"):
            raise UserError(_("Konversi yang sudah divalidasi tidak dapat dihapus."))
        return super().unlink()


class QcConversionLine(models.Model):
    _name = "qc.conversion.line"
    _description = "Stock Conversion Line"

    conversion_id = fields.Many2one("qc.conversion", required=True, ondelete="cascade", index=True)
    line_type = fields.Selection(
        [("consume", "To be Consumed"), ("produce", "To be Produced")], required=True, default="consume")
    company_id = fields.Many2one(related="conversion_id.company_id", store=True)
    product_id = fields.Many2one("product.product", string="Product", required=True, domain=[("type", "=", "product")])
    tracking = fields.Selection(related="product_id.tracking", string="Tracking")
    qty = fields.Float(string="Qty.", digits=(16, 2), required=True)
    uom_id = fields.Many2one("uom.uom", string="Unit", related="product_id.uom_id")
    location_id = fields.Many2one(
        "stock.location", string="Location", required=True, domain=[("usage", "=", "internal")])
    lot_id = fields.Many2one("stock.lot", string="Lot", domain="[('product_id', '=', product_id)]")

    def _create_move(self):
        """Barang asal keluar ke lokasi inventory adjustment, barang hasil masuk dari lokasi yang sama."""
        self.ensure_one()
        inventory_loc = self.product_id.with_company(self.company_id).property_stock_inventory
        consume = self.line_type == "consume"
        source = self.location_id if consume else inventory_loc
        dest = inventory_loc if consume else self.location_id
        conversion = self.conversion_id
        return self.env["stock.move"].sudo().create({
            "name": conversion.name,
            "origin": conversion.origin or conversion.name,
            "company_id": self.company_id.id,
            "product_id": self.product_id.id,
            "product_uom": self.uom_id.id,
            "product_uom_qty": self.qty,
            "location_id": source.id,
            "location_dest_id": dest.id,
            "qc_conversion_id": conversion.id,
            "is_inventory": True,
            "picked": True,
            "move_line_ids": [fields.Command.create({
                "picked": True,
                "product_id": self.product_id.id,
                "product_uom_id": self.uom_id.id,
                "quantity": self.qty,
                "lot_id": self.lot_id.id,
                "location_id": source.id,
                "location_dest_id": dest.id,
                "company_id": self.company_id.id,
            })],
        })
