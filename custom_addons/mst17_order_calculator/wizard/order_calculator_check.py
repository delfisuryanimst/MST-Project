from odoo import api, fields, models

from ..models.order_calculator_engine import MONTHS, _fmt


class OrderCalculatorCheck(models.TransientModel):
    _name = "order.calculator.check"
    _description = "Order Calculator: check one quotation line"

    product_id = fields.Many2one("product.product", required=True, readonly=True)
    qty = fields.Float(readonly=True, digits="Product Unit of Measure")
    product_uom_id = fields.Many2one(related="product_id.uom_id")
    need_date = fields.Date(string="Delivery Date", readonly=True)
    info = fields.Char(compute="_compute_result_html")
    result_html = fields.Html(compute="_compute_result_html", sanitize=False)

    @api.depends("product_id", "qty", "need_date")
    def _compute_result_html(self):
        engine = self.env["order.calculator.engine"]
        year = fields.Date.context_today(self).year
        for check in self:
            summary = engine._summary(check.product_id)
            date = check.need_date
            check.info = " · ".join(filter(None, [
                "%s %s" % (_fmt(check.qty), check.product_uom_id.name),
                date and "Delivery Date %s %s%s" % (date.day, MONTHS[date.month - 1],
                                                    "" if date.year == year else " %s" % date.year),
                "Siap di rak %s" % _fmt(summary["rack"]),
                "Di QC %s" % _fmt(summary["qc"]),
            ]))
            check.result_html = engine._render(check.product_id, check.qty, check.need_date)

    def action_open_calculator(self):
        self.ensure_one()
        return self.env["order.calculator"].action_open_my({
            "product_id": self.product_id.id,
            "qty": self.qty,
            "uom_id": self.product_uom_id.id,
            "mode": "date" if self.need_date else "asap",
            "need_date": self.need_date,
        })
