from odoo import _, fields, models


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    def action_order_calc_check(self):
        self.ensure_one()
        self.env["order.calculator.engine"]._warehouses()
        commitment = self.order_id.commitment_date
        wizard = self.env["order.calculator.check"].create({
            "product_id": self.product_id.id,
            "qty": self.product_uom._compute_quantity(self.product_uom_qty, self.product_id.uom_id),
            "need_date": commitment and fields.Date.context_today(self, commitment),
        })
        return {
            "type": "ir.actions.act_window",
            "name": _("Cek siap kirim · %s", " · ".join(
                part.strip() for i, part in enumerate(self.product_id.name.split(";")) if i in (0, 4))),
            "res_model": wizard._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "target": "new",
        }
