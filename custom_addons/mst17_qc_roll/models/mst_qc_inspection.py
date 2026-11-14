from odoo import _, api, fields, models
from odoo.exceptions import AccessError, UserError
from odoo.tools import float_compare, float_is_zero
from odoo.tools.misc import clean_context

from .mst_qc_alert import RESULT_TYPES

KOORDINATOR_GROUP = "mst17_qc_roll.group_qc_koordinator"


class MstQcInspection(models.Model):
    """One roll (lot) waiting in the QC location. QC fills the result; a Koordinator validates it.

    On Validate, Odoo turns the result into actions (FDD QC ID Roll, appendix B):
    Lot Warna changed -> field + history, no document. Qty scrap -> Scrap (done).
    Qty down -> Simplified MO to the Result Product (B Grade), same lot name (done).
    Result Product without Qty Down -> Simplified MO product change A to A, same lot name (done).
    Qty result different from what is left -> Simplified MO qty correction, same lot (done).
    """

    _name = "mst.qc.inspection"
    _description = "QC Roll Inspection"
    _inherit = ["mail.thread"]
    _order = "picking_id desc, id"
    _rec_name = "lot_id"

    picking_id = fields.Many2one("stock.picking", string="QCP", required=True, readonly=True, index=True)
    lot_id = fields.Many2one("stock.lot", string="Roll", required=True, readonly=True, index=True)
    product_id = fields.Many2one(related="lot_id.product_id", store=True)
    uom_id = fields.Many2one(related="product_id.uom_id", string="Unit")
    location_id = fields.Many2one(
        "stock.location", string="QC Location", required=True, readonly=True,
        help="Where the roll waits for QC. Validate needs the roll to still be there.")
    company_id = fields.Many2one(related="picking_id.company_id", store=True)
    check_id = fields.Many2one(
        "qc.check", string="Quality Check", readonly=True, index=True,
        help="The Quality Check of this roll: it holds the defect points and the yard counter.")
    yard_counter_qty = fields.Float(related="check_id.yard_counter_qty", string="Yard Counter")
    difference_qty = fields.Float(related="check_id.difference_qty", string="Difference")
    akumulasi_poin = fields.Float(related="check_id.akumulasi_poin", string="Defect Points")
    nilai_grading = fields.Float(related="check_id.nilai_grading", string="Grading")
    # Same fields and labels as the Quality Check form, read only (QC works in the check).
    check_name = fields.Char(related="check_id.name", string="Quality Check No.")
    check_point_id = fields.Many2one(related="check_id.point_id", string="QC Point")
    check_test_type = fields.Selection(related="check_id.test_type", string="Type")
    check_partner_id = fields.Many2one(related="check_id.partner_id", string="Partner")
    check_team_id = fields.Many2one(related="check_id.team_id", string="Team")
    check_user_id = fields.Many2one(related="check_id.user_id", string="QC User")
    check_date = fields.Datetime(related="check_id.control_date", string="Date")
    qty_to_test = fields.Float(related="check_id.qty_to_test", string="Quantity to Test")
    yard_asal = fields.Float(related="check_id.yard_asal", string="Yard Asal")
    pass_qty = fields.Float(related="check_id.pass_qty", string="Pass Quantity")
    fail_qty = fields.Float(related="check_id.fail_qty", string="Fail Quantity")
    qty_to_downgrade = fields.Float(related="check_id.qty_to_downgrade", string="Quantity to Downgrade")
    qty_to_scrap = fields.Float(related="check_id.qty_to_scrap", string="Quantity to Scrap")
    qty_to_sample = fields.Float(related="check_id.qty_to_sample", string="Quantity to Sample")
    check_result = fields.Selection(related="check_id.result", string="Pass/Fail")
    check_state = fields.Selection(related="check_id.quality_state", string="Status")
    lebar_inch = fields.Float(related="check_id.lebar_inch", string="Lebar (inch)")
    nilai_grading_after = fields.Float(
        related="check_id.nilai_grading_after", string="Nilai Grading (Setelah Cutting)", digits=(16, 8))
    check_description = fields.Text(related="check_id.description", string="Description")
    check_detail_ids = fields.One2many(related="check_id.detail_ids", string="Detail Cacat")
    lot_warna = fields.Char(related="lot_id.mst_lot_warna", string="Lot Warna Now")
    qty_roll = fields.Float(
        string="Qty Now", digits=(16, 2), compute="_compute_qty_roll",
        help="Qty of the roll in the QC location now.")

    lot_warna_result = fields.Char(string="Lot Warna Result", help="Empty keeps the Lot Warna as it is.")
    qty_result = fields.Float(string="Qty Result", digits=(16, 2), help="Qty that passes and stays in the roll.")
    qty_down = fields.Float(string="Qty Down", digits=(16, 2), help="Qty that goes to the B Grade product.")
    product_result_id = fields.Many2one(
        "product.product", string="Result Product", domain=[("type", "=", "product")],
        help="With Qty Down: the B Grade product of the downgrade. Without Qty Down: the roll changes to this "
             "product (A to A, reason needed). The lot name stays the same.")
    qty_scrap = fields.Float(string="Qty Scrap", digits=(16, 2))
    reason = fields.Text(help="Needed when the qty difference is above the company tolerance.")
    qty_diff = fields.Float(
        string="Qty Difference", digits=(16, 2), compute="_compute_result",
        help="Qty Now minus (Qty Result + Qty Down + Qty Scrap). Not zero means a qty correction.")
    result_type = fields.Selection(RESULT_TYPES, compute="_compute_result")
    result_preview = fields.Char(string="Odoo Will Make", compute="_compute_result")

    result_state = fields.Selection(
        [("draft", "Draft"), ("validated", "Validated")], default="draft", required=True, readonly=True,
        tracking=True)
    qty_before = fields.Float(digits=(16, 2), readonly=True, help="Qty of the roll when it was validated.")
    result_type_done = fields.Selection(RESULT_TYPES, readonly=True)
    validated_by_id = fields.Many2one("res.users", readonly=True)
    validated_date = fields.Datetime(readonly=True)
    conversion_ids = fields.Many2many(
        "qc.conversion", "mst_qc_inspection_conversion_rel", "inspection_id", "conversion_id",
        string="Simplified MOs", readonly=True)
    scrap_ids = fields.Many2many(
        "stock.scrap", "mst_qc_inspection_scrap_rel", "inspection_id", "scrap_id", string="Scraps", readonly=True)
    alert_id = fields.Many2one("mst.qc.alert", string="QC Roll Alert", readonly=True)
    piece_ids = fields.One2many(
        "mst.qc.piece", "inspection_id", string="Pieces",
        help="Fill to split the roll: at least 2 pieces. Pieces + Qty Scrap must equal the roll qty.")
    parent_id = fields.Many2one(
        "mst.qc.inspection", string="Split From", readonly=True, index=True,
        help="The split inspection this piece comes from.")
    child_ids = fields.One2many("mst.qc.inspection", "parent_id", string="Piece Inspections")

    @api.constrains("lot_id", "result_state")
    def _check_one_draft_per_roll(self):
        for ins in self.filtered(lambda i: i.result_state == "draft"):
            if self.search_count([("lot_id", "=", ins.lot_id.id), ("result_state", "=", "draft"),
                                  ("id", "!=", ins.id)]):
                raise UserError(_("%s already has an inspection to validate.", ins.lot_id.name))

    # ------------------------------------------------------------------ compute

    def _get_quants(self):
        self.ensure_one()
        return self.env["stock.quant"].sudo().search([
            ("lot_id", "=", self.lot_id.id),
            ("location_id", "child_of", self.location_id.id),
            ("quantity", ">", 0),
        ])

    @api.depends("lot_id", "location_id")
    def _compute_qty_roll(self):
        for ins in self:
            ins.qty_roll = sum(ins._get_quants().mapped("quantity")) if ins.lot_id and ins.location_id else 0.0

    def _rounding(self):
        return self.uom_id.rounding or 0.01

    # ------------------------------------------------------------------ Quality Check link

    @api.model
    def _current_location(self, lot):
        """Internal location where the roll is now (the biggest quant), e.g. QC0500; empty if none."""
        quant = self.env["stock.quant"].sudo().search([
            ("lot_id", "=", lot.id), ("location_id.usage", "=", "internal"), ("quantity", ">", 0),
        ], order="quantity desc", limit=1)
        return quant.location_id

    @api.model
    def _lot_qty(self, lot, location):
        return sum(self.env["stock.quant"].sudo().search([
            ("lot_id", "=", lot.id), ("location_id", "child_of", location.id), ("quantity", ">", 0),
        ]).mapped("quantity"))

    def action_validate_qcp(self):
        """Validate every roll still to validate on the QCP transfers of the selected lines (FSD: per QCP)."""
        pickings = self.mapped("picking_id")
        lines = self.search([("picking_id", "in", pickings.ids), ("result_state", "=", "draft")])
        if not lines:
            raise UserError(_("Nothing left to validate on %s.", ", ".join(pickings.mapped("name"))))
        return lines.action_validate_result()

    @api.model
    def _find_check(self, picking, lot):
        """The Quality Check of this roll on this transfer (the newest one); empty when there is none."""
        Check = self.env["qc.check"].sudo()
        checks = Check.search([("picking_id", "=", picking.id), ("lot_id", "=", lot.id)], order="id desc")
        if not checks:
            checks = Check.search([("picking_id", "=", picking.id), ("lot_name", "=", lot.name)], order="id desc")
        return checks[:1]

    @api.model
    def _vals_from_check(self, check, qty_roll):
        """Default result of a roll: the BS (scrap) and downgrade qty of its Quality Check, the rest passes.

        These are only defaults; the user can change them before Validate.
        """
        if not check:
            return {"qty_result": qty_roll}
        scrap, down = check.qty_to_scrap, check.qty_to_downgrade
        return {
            "check_id": check.id,
            "qty_scrap": scrap,
            "qty_down": down,
            "qty_result": max(qty_roll - scrap - down, 0.0),
        }

    def action_refresh_from_check(self):
        """Take the scrap and downgrade qty from the Quality Check again (the check may have changed)."""
        for ins in self:
            if ins.result_state != "draft":
                raise UserError(_("%s is already validated.", ins.lot_id.name))
            if not ins.check_id:
                raise UserError(_("%s has no Quality Check.", ins.lot_id.name))
            ins.write(self._vals_from_check(ins.check_id, ins.qty_roll))
        return True

    def action_open_check(self):
        self.ensure_one()
        if not self.check_id:
            raise UserError(_("%s has no Quality Check.", self.lot_id.name))
        return {
            "type": "ir.actions.act_window",
            "res_model": "qc.check",
            "res_id": self.check_id.id,
            "view_mode": "form",
        }

    def _lot_warna_changed(self):
        self.ensure_one()
        return bool(self.lot_warna_result) and self.lot_warna_result != (self.lot_id.mst_lot_warna or "")

    def _kept_qty(self):
        """Qty that stays in the roll(s): the pieces when split, else Qty Result."""
        self.ensure_one()
        return sum(self.piece_ids.mapped("qty")) if self.piece_ids else self.qty_result

    def _is_product_change(self):
        self.ensure_one()
        return not self.qty_down and bool(self.product_result_id) and self.product_result_id != self.product_id

    @api.depends("qty_roll", "qty_result", "qty_down", "qty_scrap", "lot_warna_result", "result_state",
                 "piece_ids.qty", "product_result_id")
    def _compute_result(self):
        for ins in self:
            if ins.result_state == "validated":
                ins.qty_diff = ins.qty_before - ins._kept_qty() - ins.qty_down - ins.qty_scrap
                ins.result_type = ins.result_type_done
                ins.result_preview = False
                continue
            ins.qty_diff = ins.qty_roll - ins._kept_qty() - ins.qty_down - ins.qty_scrap
            kinds, makes = [], []
            if ins._lot_warna_changed():
                kinds.append("data")
                makes.append(_("Lot Warna"))
            if ins.piece_ids:
                kinds = ["split"]  # a split stays "split" (Inspect Pieces needs it), whatever else changes
                makes.append(_("Split MO: %s new ID Roll", max(len(ins.piece_ids) - 1, 0)))
            elif ins._is_product_change():
                kinds.append("product_change")
                makes.append(_("Product Change MO, same lot"))
            else:
                if ins.qty_down > 0:
                    kinds.append("downgrade")
                    makes.append(_("Downgrade MO, same lot"))
                if not float_is_zero(ins.qty_diff, precision_rounding=ins._rounding()):
                    kinds.append("qty")
                    makes.append(_("Qty Correction MO, same lot"))
            if ins.qty_scrap > 0:
                makes.append(_("Scrap"))
                if not ins.piece_ids:
                    kinds.append("scrap")
            ins.result_type = "pass" if not kinds else kinds[0] if len(kinds) == 1 else "mixed"
            ins.result_preview = " + ".join(makes) or _("Nothing")

    def _diff_above_tolerance(self):
        self.ensure_one()
        if float_is_zero(self.qty_diff, precision_rounding=self._rounding()):
            return False
        tolerance = self.company_id.mst_qc_qty_tolerance or 0.0
        base = self.qty_roll or self.qty_before or 1.0
        return abs(self.qty_diff) / base * 100.0 > tolerance

    # ------------------------------------------------------------------ validate

    def _check_access_koordinator(self):
        if not self.env.user.has_group(KOORDINATOR_GROUP):
            raise AccessError(_("Only a QC Koordinator can validate QC results."))

    def _check_result(self):
        """Raise when this roll cannot be validated. Nothing is written here."""
        self.ensure_one()
        label = "%s (%s)" % (self.lot_id.name, self.product_id.display_name)
        rounding = self._rounding()
        if self.result_state != "draft":
            raise UserError(_("%s is already validated.", label))
        if min(self.qty_result, self.qty_down, self.qty_scrap) < 0:
            raise UserError(_("%s: quantities cannot be negative.", label))
        if float_is_zero(self.qty_roll, precision_rounding=rounding):
            raise UserError(_("%(roll)s is no longer in %(location)s.",
                              roll=label, location=self.location_id.display_name))
        if float_compare(self.qty_down + self.qty_scrap, self.qty_roll, precision_rounding=rounding) > 0:
            raise UserError(_("%s: Qty Down + Qty Scrap is more than the qty of the roll.", label))
        if self.piece_ids:
            self._check_split(label)
            return
        product = self.product_result_id
        if self.qty_down > 0:
            if not product:
                raise UserError(_("%s: choose the Result Product (B Grade) for the Qty Down.", label))
            if product == self.product_id:
                raise UserError(_("%s: the Result Product cannot be the roll's own product.", label))
        if product and product.uom_id.category_id != self.uom_id.category_id:
            raise UserError(_("%s: the Result Product needs a unit of the same kind.", label))
        if self._is_product_change():
            if not (self.reason or "").strip():
                raise UserError(_("%s: a product change needs a reason.", label))
            if self.qty_result <= 0:
                raise UserError(_("%s: a product change needs a Qty Result.", label))
        if self._diff_above_tolerance() and not (self.reason or "").strip():
            raise UserError(_(
                "%(roll)s: Qty Result + Qty Down + Qty Scrap is %(total)s but the roll has %(qty)s. "
                "The difference is above the tolerance, so enter a reason.",
                roll=label, total=self.qty_result + self.qty_down + self.qty_scrap, qty=self.qty_roll))

    def _check_split(self, label):
        """TSD rule 4: at least 2 pieces, and pieces + scrap equal the roll exactly."""
        self.ensure_one()
        if len(self.piece_ids) < 2:
            raise UserError(_("%s: a split needs at least 2 pieces.", label))
        if any(piece.qty <= 0 for piece in self.piece_ids):
            raise UserError(_("%s: every piece needs a qty greater than 0.", label))
        if self.qty_down > 0 or self._is_product_change():
            raise UserError(_("%s: a split cannot be combined with a downgrade or a product change. Split "
                              "first, then use Inspect Pieces for that piece.", label))
        if not float_is_zero(self.qty_diff, precision_rounding=self._rounding()):
            raise UserError(_(
                "%(roll)s: the pieces (%(pieces)s) + Qty Scrap (%(scrap)s) must equal the roll qty (%(qty)s).",
                roll=label, pieces=sum(self.piece_ids.mapped("qty")), scrap=self.qty_scrap, qty=self.qty_roll))

    def action_validate_result(self):
        """Validate the selected rolls. All or nothing: any error stops every roll (TSD)."""
        self._check_access_koordinator()
        inspections = self.filtered(lambda ins: ins.result_state == "draft")
        if not inspections:
            raise UserError(_("Select at least one roll that is not validated yet."))
        for ins in inspections:      # 1. check every roll first
            ins._check_result()
        for ins in inspections:      # 2. then write
            ins._apply_result()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Validate QC Result"),
                "message": _("%s roll(s) validated.", len(inspections)),
                "type": "success",
                "next": {"type": "ir.actions.client", "tag": "soft_reload"},
            },
        }

    def _apply_result(self):
        self.ensure_one()
        lot = self.lot_id
        qty_before = self.qty_roll
        result_type = self.result_type
        above_tolerance = self._diff_above_tolerance()
        if self._lot_warna_changed():
            lot._roll_event("lot_warna", lot.mst_lot_warna, self.lot_warna_result, self.picking_id)
            lot.sudo().mst_lot_warna = self.lot_warna_result
        if self.qty_scrap > 0:
            self.scrap_ids |= self._make_scrap(self.qty_scrap)
        left = qty_before - self.qty_scrap - self.qty_down
        if self.piece_ids:
            self.conversion_ids |= self._make_split()
        elif self._is_product_change():
            self.conversion_ids |= self._make_product_change(left, self.qty_result)
        else:
            downgrade = self.qty_down > 0 and self._make_downgrade(self.qty_down)
            if not float_is_zero(left - self.qty_result, precision_rounding=self._rounding()):
                self.conversion_ids |= self._make_qty_adjust(left, self.qty_result)
            if downgrade:
                self.conversion_ids |= downgrade
                self.lot_id._mst_move_epc(downgrade.produce_line_ids.lot_id, downgrade)
        self.write({
            "result_state": "validated",
            "qty_before": qty_before,
            "result_type_done": result_type,
            "validated_by_id": self.env.user.id,
            "validated_date": fields.Datetime.now(),
        })
        if self.qty_down > 0 or self.qty_scrap > 0 or self._is_product_change() or above_tolerance:
            self.alert_id = self._make_alert()

    # ------------------------------------------------------------------ documents

    def _roll_location(self):
        """Internal location that holds the roll (the biggest quant when it is split)."""
        quants = self._get_quants().sorted("quantity", reverse=True)
        return quants[:1].location_id or self.location_id

    def _make_scrap(self, qty):
        self.ensure_one()
        scrap = self.env["stock.scrap"].create({
            "product_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "scrap_qty": qty,
            "product_uom_id": self.uom_id.id,
            "location_id": self._roll_location().id,
            "origin": self.picking_id.name,
        })
        if isinstance(scrap.action_validate(), dict):
            raise UserError(_("%s: not enough stock to scrap.", self.lot_id.name))
        self.lot_id._roll_event("scrap", qty, False, scrap)
        return scrap

    def _get_lot(self, product, name, **create_vals):
        Lot = self.env["stock.lot"].with_context(clean_context(self.env.context))
        company = self.company_id or self.env.company
        return Lot.search([
            ("name", "=", name), ("product_id", "=", product.id), ("company_id", "=", company.id),
        ], limit=1) or Lot.create(dict(create_vals, name=name, product_id=product.id, company_id=company.id))

    def _get_roll_lot(self, product):
        """Lot with the roll's name in another product; it takes over the empty roll data (PO, K3L...).
        It gets no EPC of its own: the roll's tag moves to it once the roll is empty."""
        roll = self.lot_id
        lot = self._get_lot(product, roll.name, **({"x_epc": False} if "x_epc" in roll._fields else {}))
        data = {key: value for key, value in roll._mst_roll_data().items() if value and not lot[key]}
        if roll.mst_is_roll_id and not lot.mst_is_roll_id:
            data["mst_is_roll_id"] = True
        if data:
            lot.sudo().write(data)
        return lot

    def _make_conversion(self, title, conversion_type, consume_qty, produce_items):
        """Simplified MO, validated at once. produce_items is a list of (product, qty, lot).

        The consume moves are done before the produce moves, so a roll that keeps its lot name never
        goes below zero (TSD "Stock Conversion dengan lot sama").
        """
        self.ensure_one()
        location = self._roll_location()
        line = {"location_id": location.id}
        conversion = self.env["qc.conversion"].create({
            "name": title,
            "conversion_type": conversion_type,
            "origin": "%s / %s" % (self.picking_id.name, self.lot_id.name),
            "company_id": (self.company_id or self.env.company).id,
            "consume_line_ids": [fields.Command.create(dict(
                line, line_type="consume", product_id=self.product_id.id, qty=consume_qty, lot_id=self.lot_id.id,
            ))] if consume_qty > 0 else [],
            "produce_line_ids": [fields.Command.create(dict(
                line, line_type="produce", product_id=product.id, qty=qty, lot_id=lot.id,
            )) for product, qty, lot in produce_items if qty > 0],
        })
        for lines in (conversion.consume_line_ids, conversion.produce_line_ids):
            moves = self.env["stock.move"]
            for conversion_line in lines:
                moves |= conversion_line._create_move()
            moves._action_done()
        conversion.state = "done"
        return conversion

    def _make_downgrade(self, qty):
        self.ensure_one()
        product_b = self.product_result_id
        lot_b = self._get_roll_lot(product_b)
        if not lot_b.mst_item_a_id:
            lot_b.sudo().mst_item_a_id = self.product_id
        conversion = self._make_conversion(_("Downgrade"), "downgrade", qty, [(product_b, qty, lot_b)])
        self.lot_id._roll_event("turun_grade", qty, False, conversion, _("to %s", product_b.display_name))
        lot_b._roll_event("turun_grade", False, qty, conversion, _("from %s", self.product_id.display_name))
        return conversion

    def _make_product_change(self, old_qty, new_qty):
        """A to A: the whole roll goes to the Result Product with the same lot name; the tag follows."""
        self.ensure_one()
        product = self.product_result_id
        lot = self._get_roll_lot(product)
        conversion = self._make_conversion(_("Product Change"), "product_change", old_qty, [(product, new_qty, lot)])
        self.lot_id._roll_event("ganti_produk", self.product_id.display_name, product.display_name, conversion,
                                self.reason)
        lot._roll_event("ganti_produk", self.product_id.display_name, product.display_name, conversion, self.reason)
        self.lot_id._mst_move_epc(lot, conversion)
        return conversion

    def _make_qty_adjust(self, old_qty, new_qty):
        self.ensure_one()
        conversion = self._make_conversion(
            _("Qty Correction"), "qty_adjust", old_qty, [(self.product_id, new_qty, self.lot_id)])
        self.lot_id._roll_event("qty", old_qty, new_qty, conversion, self.reason)
        return conversion

    def _make_split(self):
        """Cut the roll: the longest piece keeps the roll ID, every other piece gets a new ID Roll
        with the roll as its Lot Asal. Stock value is shared by qty (same price per unit)."""
        self.ensure_one()
        lot = self.lot_id
        keeper = self.piece_ids._get_keeper()
        Lot = self.env["stock.lot"].with_context(clean_context(self.env.context))
        company = self.company_id or self.env.company
        if keeper.lot_warna and keeper.lot_warna != (lot.mst_lot_warna or ""):
            lot._roll_event("lot_warna", lot.mst_lot_warna, keeper.lot_warna, self.picking_id)
            lot.sudo().mst_lot_warna = keeper.lot_warna
        items = [(self.product_id, keeper.qty, lot)]
        for piece in self.piece_ids - keeper:
            piece.new_lot_id = Lot.create(dict(
                lot._mst_roll_data(),
                name=self.env["ir.sequence"].next_by_code("mst.roll.id"),
                product_id=self.product_id.id,
                company_id=company.id,
                mst_origin_lot_id=lot.id,
                mst_lot_warna=piece.lot_warna or lot.mst_lot_warna,
                mst_item_a_id=lot.mst_item_a_id.id,
            ))
            items.append((self.product_id, piece.qty, piece.new_lot_id))
        total = sum(self.piece_ids.mapped("qty"))
        conversion = self._make_conversion(_("Split"), "split", total, items)
        new_ids = ", ".join((self.piece_ids - keeper).mapped("new_lot_id.name"))
        lot._roll_event("pecah", total, keeper.qty, conversion, _("New rolls: %s", new_ids))
        for piece in self.piece_ids - keeper:
            piece.new_lot_id._roll_event("pecah", False, piece.qty, conversion, _("Cut from %s", lot.name))
        return conversion

    def action_inspect_pieces(self):
        """Make a draft inspection for every piece of a validated split, to downgrade or scrap a piece."""
        self.ensure_one()
        if self.result_state != "validated" or self.result_type_done != "split":
            raise UserError(_("Only a validated split has pieces to inspect."))
        pieces_lots = self.lot_id | self.piece_ids.new_lot_id
        drafts = self.search([("lot_id", "in", pieces_lots.ids), ("result_state", "=", "draft")])
        for lot in pieces_lots - drafts.lot_id:
            quants = self.env["stock.quant"].sudo().search([
                ("lot_id", "=", lot.id), ("location_id", "child_of", self.location_id.id), ("quantity", ">", 0)])
            if quants:
                drafts |= self.create({
                    "picking_id": self.picking_id.id,
                    "lot_id": lot.id,
                    "location_id": self.location_id.id,
                    "parent_id": self.id,
                    "qty_result": sum(quants.mapped("quantity")),
                })
        action = self.env["ir.actions.act_window"]._for_xml_id("mst17_qc_roll.action_mst_qc_inspection")
        action.update({"domain": [("id", "in", drafts.ids)], "context": {}})
        return action

    def _make_alert(self):
        self.ensure_one()
        return self.env["mst.qc.alert"].sudo().create({
            "inspection_id": self.id,
            "lot_id": self.lot_id.id,
            "product_id": self.product_id.id,
            "partner_id": self.picking_id._get_qc_partner().id,
            "picking_id": self.picking_id.id,
            "company_id": (self.company_id or self.env.company).id,
            "result_type": self.result_type_done,
            "qty_before": self.qty_before,
            "qty_result": self._kept_qty(),
            "qty_down": self.qty_down,
            "qty_scrap": self.qty_scrap,
            "qty_diff": self.qty_before - self._kept_qty() - self.qty_down - self.qty_scrap,
            "reason": self.reason,
            "conversion_ids": [fields.Command.set(self.conversion_ids.ids)],
            "scrap_ids": [fields.Command.set(self.scrap_ids.ids)],
        })

    def unlink(self):
        if self.filtered(lambda ins: ins.result_state == "validated"):
            raise UserError(_("A validated roll inspection cannot be deleted."))
        return super().unlink()
