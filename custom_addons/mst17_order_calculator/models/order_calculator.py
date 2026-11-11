from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .order_calculator_engine import DEVELOPMENTS, _fmt

GREIGE = [("auto", "Sesuai data"), ("yes", "Ada / sudah booking"), ("no", "Belum ada")]


class OrderCalculator(models.Model):
    _name = "order.calculator"
    _description = "Order Calculator"
    _rec_name = "product_id"

    user_id = fields.Many2one("res.users", required=True, readonly=True, default=lambda self: self.env.user,
                              ondelete="cascade", index=True)
    product_id = fields.Many2one("product.product", string="Produk",
                                 domain="[('sale_ok', '=', True), ('type', '=', 'product')]")
    product_name = fields.Char(related="product_id.name")
    product_uom_id = fields.Many2one(related="product_id.uom_id")
    qty = fields.Float(string="Jumlah Order", digits="Product Unit of Measure")
    unit_ids = fields.Many2many("uom.uom", compute="_compute_unit_ids")
    uom_id = fields.Many2one("uom.uom", string="Satuan Customer", domain="[('id', 'in', unit_ids)]")
    product_qty = fields.Float(compute="_compute_product_qty", digits="Product Unit of Measure")
    unit_note = fields.Char(compute="_compute_product_qty")
    mode = fields.Selection([("asap", "Paling cepat kapan"), ("date", "Tanggal customer")], string="Cek Berdasarkan",
                            default="asap", required=True)
    need_date = fields.Date(string="Tanggal Customer")
    development = fields.Selection(DEVELOPMENTS, string="Development")
    process = fields.Selection("_selection_process", string="Proses", default="_all")
    approve_date = fields.Date(string="Order di-ACC", default=fields.Date.context_today)
    greige = fields.Selection(GREIGE, string="Greige", default="auto", required=True)
    result_html = fields.Html(string="Hasil", sanitize=False, readonly=True)
    # OWL menu: not stored, so it is always fresh when the form opens or an input changes.
    result_json = fields.Json(string="Hasil (OWL)", compute="_compute_result_json")

    # Display only, in Indonesian number format like the mockup (3.294,5).
    rack_qty = fields.Char(compute="_compute_summary")
    qc_qty = fields.Char(compute="_compute_summary")
    demand_qty = fields.Char(compute="_compute_summary")
    incoming_qty = fields.Char(compute="_compute_summary")
    is_fresh = fields.Boolean(compute="_compute_summary")
    data_info = fields.Char(compute="_compute_summary")

    _sql_constraints = [("user_unique", "unique(user_id)", "Setiap user hanya punya satu form Order Kalkulator.")]

    def _selection_process(self):
        return self.env["order.calculator.engine"]._process_selection()

    @api.depends("product_id")
    def _compute_summary(self):
        engine = self.env["order.calculator.engine"]
        stamp = engine._stamp()
        for calc in self:
            summary = engine._summary(calc.product_id) if calc.product_id else {}
            calc.rack_qty = _fmt(summary.get("rack", 0.0))
            calc.qc_qty = _fmt(summary.get("qc", 0.0))
            calc.demand_qty = _fmt(summary.get("demand", 0.0))
            calc.incoming_qty = _fmt(summary.get("incoming", 0.0))
            calc.is_fresh = summary.get("fresh", False)
            calc.data_info = _("Terakhir diperbarui %s", stamp["calculated"])

    @api.depends("product_id")
    def _compute_unit_ids(self):
        """The product unit plus m and yd, as in the mockup (only when they are in the same UoM category)."""
        extra = self.env.ref("uom.product_uom_meter", False) | self.env.ref("uom.product_uom_yard", False)
        for calc in self:
            uom = calc.product_uom_id
            calc.unit_ids = uom | extra.filtered(lambda u: u.category_id == uom.category_id) if uom else False

    @api.depends("qty", "uom_id", "product_id")
    def _compute_product_qty(self):
        for calc in self:
            uom, product_uom = calc.uom_id or calc.product_uom_id, calc.product_uom_id
            calc.product_qty = uom._compute_quantity(calc.qty, product_uom) if uom else calc.qty
            if not product_uom:
                calc.unit_note = False
            elif uom == product_uom:
                calc.unit_note = _("Satuan mengikuti barang di Odoo (%s).", product_uom.name)
            else:
                calc.unit_note = _("= %(qty)s %(uom)s (satuan barang di Odoo: %(uom)s)",
                                   qty=_fmt(calc.product_qty), uom=product_uom.name)

    @api.depends("product_id", "product_qty", "mode", "need_date", "development", "process", "approve_date", "greige")
    def _compute_result_json(self):
        engine = self.env["order.calculator.engine"]
        for calc in self:
            ready = calc.product_id and calc.product_qty > 0 and (calc.mode == "asap" or calc.need_date)
            calc.result_json = engine._result_values(*calc._engine_args()) if ready else False

    @api.onchange("product_id")
    def _onchange_product_id(self):
        self.uom_id = self.product_uom_id
        self.result_html = False

    @api.model
    def action_open_my(self, values=None, view=None):
        self.env["order.calculator.engine"]._warehouses()
        calc = self.search([("user_id", "=", self.env.uid)], limit=1) or self.create({})
        if values:
            calc.write(values)
            calc.action_compute()
        return {
            "type": "ir.actions.act_window",
            "name": _("Order Kalkulator"),
            "res_model": self._name,
            "res_id": calc.id,
            "views": [(self.env.ref(view).id if view else False, "form")],
            "target": "current",
        }

    def _engine_args(self):
        return self.product_id, self.product_qty, self.mode == "date" and self.need_date, {
            "development": self.development,
            "process": self.process,
            "approve_date": self.approve_date,
            "greige": self.greige,
        }

    def action_compute(self):
        self.ensure_one()
        if not self.product_id or self.qty <= 0:
            raise UserError(_("Isi produk dan jumlah order lebih dari nol."))
        if self.mode == "date" and not self.need_date:
            raise UserError(_("Isi tanggal customer."))
        self.result_html = self.env["order.calculator.engine"]._render(*self._engine_args())

    def action_reset(self):
        self.write({
            "product_id": False, "qty": 0.0, "uom_id": False, "mode": "asap", "need_date": False,
            "development": False, "process": "_all", "approve_date": fields.Date.context_today(self),
            "greige": "auto", "result_html": False,
        })
