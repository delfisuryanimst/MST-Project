import json

from odoo import api, fields, models
from odoo.tools.misc import format_amount

from .sale_order_line import JOURNEY_CONCLUSIONS, JOURNEY_STAGES, _fmt

CONCLUSION_COLORS = {"ready": "ok", "waiting_qc": "warn", "short": "bad"}


class SaleOrder(models.Model):
    _inherit = "sale.order"

    # Built fresh each time the form is opened; nothing is stored.
    journey_html = fields.Html(compute="_compute_journey_html", sanitize=False)

    @api.depends("order_line")
    def _compute_journey_html(self):
        qweb = self.env["ir.qweb"]
        for order in self:
            lines = order.order_line.filtered(lambda l: not l.display_type and not l.is_downpayment)
            cards = [order._journey_card(json.loads(line.journey_data)) for line in lines]
            order.journey_html = qweb._render("mst17_sales_journey.journey_cards", {"cards": cards})

    def _journey_card(self, data):
        """Turn the journey data of one line into ready-to-show texts."""
        uom = data["uom"]
        currency = self.currency_id
        open_line = data["order_state"] == "sale" and data["remaining"] > 0

        if data["shared"]:
            badge = {"text": "Shared Production", "color": "info"}
        elif data["conclusion"] == "short":
            badge = {"text": "Incomplete, missing %s %s" % (_fmt(data["short_qty"]), uom), "color": "bad"}
        elif data["conclusion"]:
            badge = {"text": dict(JOURNEY_CONCLUSIONS)[data["conclusion"]],
                     "color": CONCLUSION_COLORS[data["conclusion"]]}
        else:
            badge = {"text": dict(JOURNEY_STAGES)[data["stage"]], "color": "muted"}

        facts, notes = [], []
        # Shared production: the stock numbers are for several orders, so they are not shown.
        if open_line and not data["shared"]:
            facts.append({"label": "Ready on Rack", 
                          "value": "%s %s" % (_fmt(data["rack"]), uom)})
            # Sold from stock: no QC batch is tracked for the line, so the QC box is left out.
            qc_batches = [] if data["from_stock"] else (data["qc_batches"] or [None])
            for batch in qc_batches:
                if batch is None:
                    facts.append({"label": "In %s" % data["qc_location_name"], "value": "0 %s" % uom})
                    continue
                arrived = fields.Date.to_date(batch["date"]).strftime("%d %b")
                facts.append({
                    "label": "In %s (arrived %s)" % (data["qc_location_name"], arrived),
                    "value": "%s %s · %s days" % (_fmt(batch["qty"]), uom, batch["age"]),
                    "warning": batch["warning"],
                })
                if batch["warning"]:
                    notes.append({
                        "icon": "fa-exclamation-triangle",
                        "color": "danger",
                        "text": "Waiting inspection for more than %s days (arrived %s), "
                                "confirm the schedule with PPIC." % (data["qc_warning_days"], arrived),
                    })
            facts.append({
                "label": "Not Available" if data["from_stock"] else "Not Arrived",
                "value": "%s %s" % (_fmt(data["not_arrived"]), uom),
            })

        invoice = data["invoice"]
        if invoice["count"] or (open_line and not data["shared"]):
            facts.append({
                "label": "Invoice",
                "value": format_amount(self.env, invoice["total"], currency),
                "sub": "due %s" % format_amount(self.env, invoice["residual"], currency),
            })

        if data["shared"]:
            notes.append({
                "icon": "fa-info-circle",
                "color": "info",
                "text": "The quantity is the total of a shared production, also for %s; "
                        "please confirm with PPIC." % " & ".join(data["shared_orders"]),
            })

        return {
            "product": data["product"],
            "subtitle": "%s %s · delivered %s · remaining %s" % (
                _fmt(data["qty"]), uom, _fmt(data["delivered"]), _fmt(data["remaining"])),
            "badge": badge,
            "steps": data["steps"],
            "facts": facts,
            "notes": notes,
        }
