from markupsafe import Markup

from odoo import _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context


class QcConversion(models.Model):
    """Simplified MO: turns the source product (Grade A) into the result product (B Grade)."""

    _name = "qc.conversion"
    _description = "Simplified MO"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    reference = fields.Char(default=lambda self: _("New"), copy=False, readonly=True, index=True)
    name = fields.Char(required=True, copy=False, default=lambda self: _("Downgrade"), tracking=True)
    date = fields.Datetime(default=fields.Datetime.now, required=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company)
    origin = fields.Char(string="Source Document")
    alert_id = fields.Many2one("qc.alert", string="Quality Alert", index=True, copy=False)
    conversion_type = fields.Selection(
        [("downgrade", "Downgrade"), ("qty_adjust", "Qty Correction")],
        string="Type", default="downgrade", required=True, copy=False,
        help="Downgrade: moves qty to another product. Qty Correction: same product and lot, only the qty "
             "changes. Only Downgrade counts in the Downgrade Qty of the Quality Alert.")
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
    qcp_id = fields.Many2one(
        "stock.picking", string="QCP", compute="_compute_qcp_id", store=True, readonly=True, copy=False,
        help="The open QC transfer that holds the consumed lot. On Validate the results are put in it, so "
             "validating the transfer moves them on with the other rolls.")

    @api.depends("alert_id.picking_id")
    def _compute_qcp_id(self):
        for conversion in self:
            conversion.qcp_id = conversion.alert_id.picking_id

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # An alert can have several Simplified MOs (for example a downgrade and a qty change).
            if vals.get("reference", _("New")) == _("New"):
                vals["reference"] = self.env["ir.sequence"].next_by_code("qc.conversion") or _("New")
        conversions = super().create(vals_list)
        for conversion in conversions.filtered("alert_id"):
            conversion.message_post(body=Markup('%s: <a href="#" data-oe-model="qc.alert" data-oe-id="%s">%s</a>') % (
                _("This downgrade has been created from"), conversion.alert_id.id, conversion.alert_id.name))
        return conversions

    @api.depends("consume_line_ids.qty", "produce_line_ids.qty")
    def _compute_totals(self):
        for conversion in self:
            conversion.consume_total = sum(conversion.consume_line_ids.mapped("qty"))
            conversion.produce_total = sum(conversion.produce_line_ids.mapped("qty"))

    def action_generate_lot(self):
        """Create the missing lot of tracked result lines, named after the source lot."""
        self.ensure_one()
        self = self.with_context(clean_context(self.env.context))
        source_lot = self.consume_line_ids.lot_id[:1]
        for line in self.produce_line_ids.filtered(lambda l: l.tracking != "none" and not l.lot_id):
            if not source_lot:
                raise UserError(_("Enter the lot on the To be Consumed item first."))
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
            raise UserError(_("Fill in both the To be Consumed and To be Produced items first."))
        for line in self.consume_line_ids | self.produce_line_ids:
            if line.qty <= 0:
                raise UserError(_("Quantity must be greater than 0: %s", line.product_id.display_name))
            if line.tracking != "none" and not line.lot_id:
                raise UserError(_("A lot is required for: %s", line.product_id.display_name))

    def action_validate(self):
        self = self.with_context(clean_context(self.env.context))
        for conversion in self:
            if conversion.state != "draft":
                continue
            conversion._check_ready()
            freed = conversion._qcp_release()
            # Consume first, then produce, so a lot used on both sides never goes below zero.
            for lines in (conversion.consume_line_ids, conversion.produce_line_ids):
                moves = self.env["stock.move"]
                for line in lines:
                    moves |= line._create_move()
                moves._action_done()
            vals = {"state": "done"}
            if freed:
                vals["qcp_id"] = conversion._qcp_add(freed).id
            conversion.write(vals)
        return True

    # ------------------------------------------------------------------ QC transfer (QCP) sync

    def _qcp_release(self):
        """Before the stock moves: lower what open internal transfers reserved on the consumed lots.

        Without this the transfer keeps a reservation on stock that the SMO takes away.
        Returns the transfer lines that were lowered.
        """
        self.ensure_one()
        MoveLine = self.env["stock.move.line"]
        freed = MoveLine
        for line in self.consume_line_ids.filtered("lot_id"):
            left = line.qty
            for ml in MoveLine.search([
                ("product_id", "=", line.product_id.id),
                ("lot_id", "=", line.lot_id.id),
                ("location_id", "child_of", line.location_id.id),
                ("state", "not in", ("done", "cancel")),
                ("picking_id.picking_type_id.code", "=", "internal"),
            ], order="quantity desc, id"):
                take = min(ml.quantity, left)
                if take <= 0:
                    break
                ml.quantity -= take
                move = ml.move_id.with_context(do_not_unreserve=True)
                move.product_uom_qty = max(move.product_uom_qty - take, 0.0)
                left -= take
                freed |= ml
        return freed

    def _qcp_add(self, freed):
        """After the stock moves: put every result in the transfer that held the consumed lot.

        A result of the same product and lot goes back on its line; any other result gets a new line
        (and a new move for a new product) with the same Receiving Status. Returns the transfer.
        """
        self.ensure_one()
        picking = freed.picking_id.sorted("id")[:1]
        template = freed.filtered(lambda ml: ml.picking_id == picking)[:1]
        MoveLine = self.env["stock.move.line"]
        for line in self.produce_line_ids:
            move = picking.move_ids.filtered(
                lambda m: m.product_id == line.product_id and m.state not in ("done", "cancel"))[:1]
            if move:
                move.with_context(do_not_unreserve=True).product_uom_qty += line.qty
            else:
                move = self.env["stock.move"].create({
                    "name": line.product_id.display_name,
                    "product_id": line.product_id.id,
                    "product_uom": line.uom_id.id,
                    "product_uom_qty": line.qty,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "picking_id": picking.id,
                    "company_id": picking.company_id.id,
                    "origin": self.reference,
                })
                move._action_confirm(merge=False)
                # Confirm may reserve any lot at once; only the result lot belongs to this move.
                move._do_unreserve()
            ml = picking.move_line_ids.filtered(
                lambda m: m.move_id == move and m.lot_id == line.lot_id and m.state not in ("done", "cancel"))[:1]
            if ml:
                ml.quantity += line.qty
            else:
                MoveLine.create({
                    "move_id": move.id,
                    "picking_id": picking.id,
                    "product_id": line.product_id.id,
                    "product_uom_id": line.uom_id.id,
                    "lot_id": line.lot_id.id,
                    "quantity": line.qty,
                    "location_id": line.location_id.id,
                    "location_dest_id": template.location_dest_id.id or picking.location_dest_id.id,
                    "receiving_status": template.receiving_status,
                    "qc_request_status": template.qc_request_status,
                    "company_id": picking.company_id.id,
                })
        # Lines whose lot was fully taken and did not come back are removed.
        freed.exists().filtered(lambda ml: ml.quantity <= 0 and ml.state not in ("done", "cancel")).unlink()
        picking.message_post(body=_("%(smo)s changed the rolls of this transfer: %(changes)s", smo=self.reference,
                                    changes=", ".join("%s %s %s" % (l.product_id.display_name, l.lot_id.name or "", l.qty)
                                                      for l in self.produce_line_ids)))
        return picking

    def action_open_alert(self):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_alert_action")
        action.update({"views": [(False, "form")], "res_id": self.alert_id.id})
        return action

    def action_cancel(self):
        if self.filtered(lambda c: c.state == "done"):
            raise UserError(_("A validated conversion cannot be cancelled."))
        self.write({"state": "cancel"})

    def action_draft(self):
        self.filtered(lambda c: c.state == "cancel").write({"state": "draft"})

    def unlink(self):
        if self.filtered(lambda c: c.state == "done"):
            raise UserError(_("A validated conversion cannot be deleted."))
        return super().unlink()


class QcConversionLine(models.Model):
    _name = "qc.conversion.line"
    _description = "Simplified MO Line"

    conversion_id = fields.Many2one("qc.conversion", required=True, ondelete="cascade", index=True)
    line_type = fields.Selection(
        [("consume", "To be Consumed"), ("produce", "To be Produced")], required=True, default="consume")
    company_id = fields.Many2one(related="conversion_id.company_id", store=True)
    product_id = fields.Many2one("product.product", required=True, domain=[("type", "=", "product")])
    tracking = fields.Selection(related="product_id.tracking")
    qty = fields.Float(string="Qty.", digits=(16, 2), required=True)
    uom_id = fields.Many2one("uom.uom", string="Unit", related="product_id.uom_id")
    location_id = fields.Many2one("stock.location", required=True, domain=[("usage", "=", "internal")])
    lot_id = fields.Many2one("stock.lot", domain="[('product_id', '=', product_id)]")

    @api.onchange("product_id")
    def _onchange_product_id_fill(self):
        """A new To be Produced line takes the location of the consumed item and the qty not produced yet."""
        if self.line_type != "produce" or not self.product_id:
            return
        conversion = self.conversion_id
        if not self.location_id:
            self.location_id = conversion.consume_line_ids.location_id[:1]
        if not self.qty:
            produced = sum(conversion.produce_line_ids.mapped("qty"))
            self.qty = max(sum(conversion.consume_line_ids.mapped("qty")) - produced, 0.0)

    def _create_move(self):
        """Source goods leave to the inventory adjustment location; result goods come in from it."""
        self.ensure_one()
        inventory_loc = self.product_id.with_company(self.company_id).property_stock_inventory
        consume = self.line_type == "consume"
        source = self.location_id if consume else inventory_loc
        dest = inventory_loc if consume else self.location_id
        conversion = self.conversion_id
        return self.env["stock.move"].sudo().create({
            "name": conversion.reference,
            "origin": conversion.origin or conversion.reference,
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
