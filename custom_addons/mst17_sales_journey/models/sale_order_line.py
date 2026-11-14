import json
from collections import defaultdict

from odoo import api, fields, models
from odoo.osv import expression
from odoo.tools import split_every

JOURNEY_STAGES = [
    ("quotation", "Quotation / Booking"),
    ("production", "In Production"),
    ("qc", "Arrived, Waiting QC"),
    ("ready", "Ready on Rack"),
    ("partial", "Partially Delivered"),
    ("to_invoice", "Delivered, To Invoice"),
    ("done", "Done"),
    ("cancel", "Cancelled"),
]

JOURNEY_CONCLUSIONS = [
    ("ready", "Ready to Deliver"),
    ("waiting_qc", "In Warehouse, Waiting Inspection"),
    ("short", "Incomplete"),
]

INVOICE_STATES = [
    ("none", "Not Invoiced"),
    ("partial", "Partially Invoiced"),
    ("full", "Fully Invoiced"),
]

# The cron saves its work after every batch, so no row stays locked for the whole run.
CRON_BATCH_SIZE = 500


class SaleOrderLine(models.Model):
    _inherit = "sale.order.line"

    # Full journey of the line as JSON. Not stored and not used by the screens:
    # it only exists for other readers (e.g. an API) and costs nothing until it is read.
    journey_data = fields.Text(compute="_compute_journey_data")

    # Stored copy of the main numbers, filled by the cron, so the list can filter and group.
    journey_stage = fields.Selection(JOURNEY_STAGES, string="Current Stage", readonly=True, index=True)
    journey_conclusion = fields.Selection(JOURNEY_CONCLUSIONS, string="Conclusion", readonly=True)
    journey_short_qty = fields.Float(string="Missing Quantity", readonly=True, digits="Product Unit of Measure")
    journey_qty_remaining = fields.Float(string="Remaining to Deliver", readonly=True,
                                         digits="Product Unit of Measure")
    journey_qc_age = fields.Integer(string="QC Age (Days)", readonly=True,
                                    help="Days since the oldest batch still in the QC location arrived.")
    journey_qc_warning = fields.Boolean(string="Waiting QC Too Long", readonly=True,
                                        help="A batch has been in the QC location longer than the warning limit.")
    journey_shared = fields.Boolean(string="Shared Production", readonly=True,
                                    help="The purchase behind this line is also linked to another sales order.")
    journey_overdue = fields.Boolean(string="Late", readonly=True,
                                     help="Delivery Date has passed and there is still quantity to deliver.")
    journey_invoice_state = fields.Selection(INVOICE_STATES, string="Invoiced", readonly=True)
    journey_updated = fields.Datetime(string="Journey Updated On", readonly=True)
    # Stored copies of order and product values, only so the list can sort and group by them.
    journey_delivery_date = fields.Datetime(related="order_id.commitment_date", store=True)
    journey_team_id = fields.Many2one(related="order_id.team_id", store=True)
    journey_categ_id = fields.Many2one(related="product_id.categ_id", store=True)

    def _compute_journey_data(self):
        settings = self._journey_settings()
        for line in self:
            line.journey_data = json.dumps(line._journey_build(settings))

    # ------------------------------------------------------------------
    # Stored status (cron and the "Update Journey" action)
    # ------------------------------------------------------------------

    @api.model
    def _journey_cron_domain(self):
        """Only lines whose stored status can still change."""
        return expression.AND([
            [("display_type", "=", False), ("is_downpayment", "=", False)],
            expression.OR([
                [("journey_stage", "=", False)],
                [("order_id.state", "=", "sale"), ("journey_stage", "not in", ("done", "cancel"))],
                [("order_id.state", "in", ("draft", "sent")), ("journey_stage", "!=", "quotation")],
                [("order_id.state", "=", "cancel"), ("journey_stage", "!=", "cancel")],
            ]),
        ])

    @api.model
    def _cron_update_journey(self):
        line_ids = self.search(self._journey_cron_domain()).ids
        for batch_ids in split_every(CRON_BATCH_SIZE, line_ids):
            self.browse(batch_ids)._journey_refresh()
            self.env.cr.commit()
            self.env.invalidate_all()

    def _journey_refresh(self):
        """Save the current journey of each line, writing only what changed."""
        settings = self._journey_settings()
        changes = defaultdict(lambda: self.browse())  # new values -> lines that get them
        for line in self:
            values = line._journey_stored_values(line._journey_build(settings))
            if line._journey_has_changed(values):
                changes[tuple(values.items())] |= line
        for values, lines in changes.items():
            lines.write(dict(values))
        self.write({"journey_updated": fields.Datetime.now()})

    def _journey_stored_values(self, data):
        return {
            "journey_stage": data["stage"],
            "journey_conclusion": data["conclusion"],
            "journey_short_qty": data["short_qty"],
            "journey_qty_remaining": data["remaining"],
            "journey_qc_age": data["qc_age"],
            "journey_qc_warning": any(batch["warning"] for batch in data["qc_batches"]),
            "journey_shared": data["shared"],
            "journey_overdue": data["overdue"],
            "journey_invoice_state": data["invoice"]["state"],
        }

    def _journey_has_changed(self, values):
        """Compare in the field's own precision, so 0.1 + 0.2 does not count as a change."""
        self.ensure_one()
        return any(
            self[name] != self._fields[name].convert_to_cache(value, self)
            for name, value in values.items()
        )

    def action_journey_refresh(self):
        self._journey_refresh()

    def action_open_journey_order(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "sale.order",
            "res_id": self.order_id.id,
            "view_mode": "form",
            "target": "current",
        }

    # ------------------------------------------------------------------
    # Journey build
    # ------------------------------------------------------------------

    @api.model
    def _journey_settings(self):
        params = self.env["ir.config_parameter"].sudo()
        qc_name = params.get_param("mst17_sales_journey.qc_location_name", "QC0500")
        Location = self.env["stock.location"].sudo()
        qc_locations = Location.search([("name", "=", qc_name), ("usage", "=", "internal")])
        if qc_locations:
            qc_locations = Location.search([("id", "child_of", qc_locations.ids)])
        return {
            "qc_location_name": qc_name,
            "qc_location_ids": qc_locations.ids,
            "qc_warning_days": int(params.get_param("mst17_sales_journey.qc_warning_days", 14)),
            "today": fields.Date.context_today(self),
            "now": fields.Datetime.now(),
        }

    def _journey_build(self, settings):
        """Collect everything about one line. Runs as superuser, but only summary
        numbers leave this method: no vendor name, no PO / receipt number."""
        self.ensure_one()
        line = self.sudo()
        order = line.order_id
        qty = line.product_uom_qty
        delivered = line.qty_delivered
        remaining = max(qty - delivered, 0.0)
        confirmed = order.state == "sale"

        # Quotations and cancelled orders have no purchase to follow, and a fully
        # delivered line does not need stock numbers: skip those lookups.
        purchase_lines = line._journey_purchase_lines() if confirmed else line.env["purchase.order.line"]
        shared_orders = line._journey_shared_orders(purchase_lines)
        rack, qc_batches = (0.0, [])
        if confirmed and remaining > 0:
            rack, qc_batches = line._journey_stock(purchase_lines, settings)

        qc_total = sum(batch["qty"] for batch in qc_batches)
        qc_age = max((batch["age"] for batch in qc_batches), default=0)
        not_arrived = max(remaining - rack - qc_total, 0.0)
        invoice = line._journey_invoice()
        overdue = bool(
            confirmed and remaining > 0
            and order.commitment_date and order.commitment_date < settings["now"]
        )
        shared = bool(shared_orders)
        conclusion, short_qty = line._journey_conclusion(confirmed and remaining > 0 and not shared,
                                                         remaining, rack, qc_total)

        return {
            "product": line.product_id.display_name,
            "uom": line.product_uom.name,
            "qty": qty,
            "delivered": delivered,
            "remaining": remaining,
            "rack": rack,
            "qc_total": qc_total,
            "qc_batches": qc_batches,
            "qc_age": qc_age,
            "qc_warning_days": settings["qc_warning_days"],
            "qc_location_name": settings["qc_location_name"],
            "not_arrived": not_arrived,
            "invoice": invoice,
            "shared": shared,
            "shared_orders": shared_orders.mapped("name"),
            "from_stock": confirmed and not purchase_lines,
            "overdue": overdue,
            "order_state": order.state,
            "stage": line._journey_stage(remaining, rack, qc_total),
            "conclusion": conclusion,
            "short_qty": short_qty,
            "steps": line._journey_steps(purchase_lines, remaining, rack, qc_total, invoice),
        }

    def _journey_conclusion(self, applies, remaining, rack, qc_total):
        """Return (conclusion, missing quantity)."""
        if not applies:
            return False, 0.0
        if remaining <= rack:
            return "ready", 0.0
        if remaining <= rack + qc_total:
            return "waiting_qc", 0.0
        return "short", remaining - rack - qc_total

    def _journey_purchase_lines(self):
        """Purchase lines that supply this line (make-to-order chain)."""
        if not self.move_ids:
            return self.env["purchase.order.line"]
        chain = self.env["stock.move"].browse(self.move_ids._rollup_move_origs())
        purchase_lines = chain.created_purchase_line_ids | chain.purchase_line_id
        return purchase_lines.filtered(lambda p: p.state != "cancel")

    def _journey_shared_orders(self, purchase_lines):
        """Other sales orders supplied by the same purchase lines."""
        if not purchase_lines:
            return self.env["sale.order"]
        dest_moves = purchase_lines.move_dest_ids
        dest_moves |= self.env["stock.move"].browse(purchase_lines.move_ids._rollup_move_dests())
        return dest_moves.sale_line_id.order_id - self.order_id

    def _journey_stock(self, purchase_lines, settings):
        """Return (qty on rack, list of QC batches) in the line unit.
        With a linked purchase, follow the lots it received. Without one
        (sold from stock), use what is reserved on the pending delivery.
        Quantities are summed in the database: a purchase can hold thousands of lots."""
        uom = self.product_id.uom_id

        if not purchase_lines:
            pending = self.move_ids.filtered(
                lambda m: m.state in ("assigned", "partially_available")
                and m.location_dest_id.usage == "customer"
            )
            return uom._compute_quantity(sum(pending.mapped("quantity")), self.product_uom), []

        received = purchase_lines.move_ids.filtered(lambda m: m.state == "done")
        # Arrival date of a lot = when its receipt was first validated.
        arrival_by_lot = {
            lot.id: arrival for lot, arrival in self.env["stock.move.line"]._read_group(
                [("move_id", "in", received.ids), ("lot_id", "!=", False)],
                groupby=["lot_id"], aggregates=["date:min"],
            )
        }
        if not arrival_by_lot:
            return 0.0, []

        quant_domain = [
            ("lot_id", "in", list(arrival_by_lot)),
            ("location_id.usage", "=", "internal"),
            ("quantity", ">", 0),
        ]
        Quant = self.env["stock.quant"]
        qc_ids = settings["qc_location_ids"]

        rack = Quant._read_group(
            quant_domain + [("location_id", "not in", qc_ids)], aggregates=["quantity:sum"],
        )[0][0] or 0.0

        qc_by_date = defaultdict(float)
        if qc_ids:
            for lot, quantity in Quant._read_group(
                quant_domain + [("location_id", "in", qc_ids)],
                groupby=["lot_id"], aggregates=["quantity:sum"],
            ):
                qc_by_date[fields.Date.to_date(arrival_by_lot[lot.id])] += quantity

        batches = []
        for arrival, quantity in sorted(qc_by_date.items()):
            age = (settings["today"] - arrival).days
            batches.append({
                "date": arrival.isoformat(),
                "qty": uom._compute_quantity(quantity, self.product_uom),
                "age": age,
                "warning": age > settings["qc_warning_days"],
            })
        return uom._compute_quantity(rack, self.product_uom), batches

    def _journey_invoice(self):
        invoice_lines = self.invoice_lines.filtered(
            lambda l: l.move_id.state == "posted" and l.move_id.move_type in ("out_invoice", "out_refund")
        )
        total = residual = 0.0
        for inv_line in invoice_lines:
            move = inv_line.move_id
            sign = -1 if move.move_type == "out_refund" else 1
            total += sign * inv_line.price_total
            if move.amount_total:
                residual += sign * inv_line.price_total * move.amount_residual / move.amount_total

        if self.qty_invoiced <= 0:
            state = "none"
        elif self.qty_invoiced >= self.product_uom_qty:
            state = "full"
        else:
            state = "partial"
        return {
            "state": state,
            "count": len(invoice_lines.move_id.filtered(lambda m: m.move_type == "out_invoice")),
            "total": total,
            "residual": residual,
            "currency": self.currency_id.name,
        }

    def _journey_stage(self, remaining, rack, qc_total):
        state = self.order_id.state
        if state == "cancel":
            return "cancel"
        if state in ("draft", "sent"):
            return "quotation"
        if remaining <= 0:
            return "to_invoice" if self.qty_invoiced < self.qty_delivered else "done"
        if qc_total > 0:
            return "qc"
        if self.qty_delivered > 0:
            return "partial"
        if rack > 0:
            return "ready"
        return "production"

    # ------------------------------------------------------------------
    # Steps for the Journey tab
    # ------------------------------------------------------------------

    def _journey_booking_step(self):
        """Booking from mst17_sales_booking, when installed: the line's stock reservation
        (Book Stock), else the order's Booking Form. Like the Mobile app, the step is only
        shown when there is a booking."""
        order = self.order_id
        if "reservation_ids" not in order._fields:
            return None
        reservation = order.reservation_ids.filtered(
            lambda r: r.sale_order_line_id == self and r.booking_status != "released")[:1]
        if reservation:
            return {
                "key": "booking_fg",
                "title": "Finished Goods Booking",
                "done": True,
                "info": "%s · %s %s" % (reservation.name, _fmt(reservation.product_uom_qty),
                                        reservation.product_uom.name),
            }
        booking = order.booking_id
        if booking.state not in ("to_approve", "booked", "closed"):
            return None
        lines = booking.line_ids.filtered(lambda b: b.product_id == self.product_id) or booking.line_ids
        qty = sum(lines.mapped("product_uom_qty"))
        return {
            "key": "booking_rm",
            "title": "Greige Booking",
            "done": booking.state != "to_approve",
            "info": "%s · %s %s" % (booking.name, _fmt(qty), lines[:1].product_uom.name),
        }

    def _journey_production_step(self, purchase_lines):
        """Production / purchase. Subcontracting stages (e.g. Dyeing) can be
        added here once the subkon model is available."""
        if self.order_id.state != "sale":
            # Before confirmation there is no delivery or purchase yet, so we cannot tell.
            return {"key": "process", "title": "Production", "done": False, "info": ""}
        if not purchase_lines:
            return {"key": "process", "title": "Production", "done": True, "skipped": True,
                    "info": "Not needed"}
        open_lines = purchase_lines.filtered(lambda p: p.qty_received < p.product_qty)
        if not open_lines:
            return {"key": "process", "title": "Production", "done": True, "info": "Done"}
        planned = min(open_lines.mapped("date_planned"))
        return {
            "key": "process",
            "title": "Production",
            "done": False,
            "info": "Expected %s" % fields.Date.to_date(planned).strftime("%d %b"),
        }

    def _journey_steps(self, purchase_lines, remaining, rack, qc_total, invoice):
        order = self.order_id
        qty = self.product_uom_qty
        confirmed = order.state == "sale"
        arrived = min(self.qty_delivered + rack + qc_total, qty)
        # Sold from stock: nothing is received for this line, it only waits for a reservation.
        from_stock = confirmed and not purchase_lines

        steps = [{
            "key": "quotation",
            "title": "Quotation",
            "done": order.state in ("sale", "sent"),
            "info": order.create_date.strftime("%d %b") if order.create_date else "",
        }]
        booking = self._journey_booking_step()
        if booking:
            steps.append(booking)
        steps += [
            {
                "key": "confirm",
                "title": "Sales Order",
                "done": confirmed,
                "info": order.date_order.strftime("%d %b") if confirmed else "",
            },
            self._journey_production_step(purchase_lines),
            {
                "key": "arrival",
                "title": "Reserved" if from_stock else "Receipt & QC",
                "done": confirmed and arrived >= qty and qc_total <= 0,
                "info": "%s / %s" % (_fmt(arrived), _fmt(qty)),
            },
            {
                "key": "delivery",
                "title": "Delivery",
                "done": confirmed and remaining <= 0,
                "info": "%s / %s" % (_fmt(self.qty_delivered), _fmt(qty)),
            },
            {
                "key": "invoice",
                "title": "Invoice",
                "done": invoice["state"] == "full",
                "info": "%s posted" % invoice["count"] if invoice["count"] else "-",
            },
        ]

        # The first step that is not done is the current one.
        current_found = order.state == "cancel"
        for step in steps:
            if step.get("skipped"):
                step["status"] = "skipped"
            elif step["done"]:
                step["status"] = "done"
            elif not current_found:
                step["status"] = "current"
                current_found = True
            else:
                step["status"] = "todo"
        return steps


def _fmt(number):
    """1234.5 -> '1,234.5' (no trailing zeros)."""
    return ("{:,.2f}".format(number)).rstrip("0").rstrip(".")
