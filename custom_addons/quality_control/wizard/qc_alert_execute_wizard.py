from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context


class QcAlertExecuteWizard(models.TransientModel):
    """Bulk Execute: make a draft Scrap and/or a draft Simplified MO for many Quality Alerts at once.

    Nothing moves stock here. The user checks the drafts afterwards (and edits a Simplified MO that
    has to make several products), then validates them.
    """

    _name = "qc.alert.execute.wizard"
    _description = "Execute Quality Alerts"

    # All alerts run in one request; a small batch never comes near the server time limit.
    MAX_ALERTS = 50

    line_ids = fields.One2many("qc.alert.execute.wizard.line", "wizard_id", string="Alerts")
    split_ids = fields.One2many(
        "qc.alert.execute.wizard.split", "wizard_id", string="Split Result",
        help="Only for an alert whose Downgrade Qty makes several products: one row per product.")
    alert_ids = fields.Many2many("qc.alert", compute="_compute_alert_ids")
    note = fields.Char(readonly=True)
    team_id = fields.Many2one(
        "qc.team", default=lambda self: self.env.user._get_qc_team(),
        help="Set on every alert that gets a draft. Clear it to keep the team of each alert.")
    user_id = fields.Many2one(
        "res.users", string="Responsible", default=lambda self: self.env.user,
        help="Set on every alert that gets a draft. Clear it to keep the responsible of each alert.")

    @api.model
    def default_get(self, fields_list):
        values = super().default_get(fields_list)
        if self.env.context.get("active_model") != "qc.alert":
            return values
        selected = self.env["qc.alert"].browse(self.env.context.get("active_ids", []))
        # An alert with a Scrap and an open Simplified MO, or with nothing left to downgrade, has nothing to do.
        alerts = selected.filtered(lambda alert: alert._has_work_left())
        if not alerts:
            raise UserError(_(
                "Nothing left to execute: each of the %s selected Quality Alerts already has a Scrap and "
                "a Simplified MO (draft or done).", len(selected)))
        self._check_max_alerts(len(alerts))
        if len(alerts) < len(selected):
            values["note"] = _(
                "%(shown)s of %(total)s alerts are listed. The other %(left)s already have a Scrap and a "
                "Simplified MO.", shown=len(alerts), total=len(selected), left=len(selected) - len(alerts))
        values["line_ids"] = [Command.create({
            "alert_id": alert.id,
            "scrap_qty": 0.0 if alert.scrap_ids else max(alert._scrap_remaining(), 0.0),
            "downgrade_qty": 0.0 if alert._active_conversion() else alert._downgrade_remaining(),
        }) for alert in alerts]
        return values

    @api.depends("line_ids.alert_id")
    def _compute_alert_ids(self):
        for wizard in self:
            wizard.alert_ids = wizard.line_ids.alert_id

    @api.model
    def _check_max_alerts(self, count):
        if count > self.MAX_ALERTS:
            raise UserError(_(
                "%(count)s alerts are listed. Execute at most %(max)s alerts at a time.",
                count=count, max=self.MAX_ALERTS))

    def action_create_drafts(self):
        self.ensure_one()
        self._check_max_alerts(len(self.line_ids))
        if any(line.scrap_qty < 0 or line.downgrade_qty < 0 for line in self.line_ids):
            raise UserError(_("Quantities cannot be negative."))
        if self.split_ids.alert_id - self.line_ids.alert_id:
            raise UserError(_("A Split Result row points to an alert that is not in the list."))
        assignment = {key: record.id for key, record in (("team_id", self.team_id), ("user_id", self.user_id))
                      if record}
        skipped = []
        counts = {"scraps": 0, "conversions": 0, "untouched": 0}
        for line in self.line_ids:
            alert = line.alert_id
            splits = self.split_ids.filtered(lambda split: split.alert_id == alert)
            do_scrap, do_downgrade = line.scrap_qty > 0, line.downgrade_qty > 0
            if not (do_scrap or do_downgrade or splits):
                counts["untouched"] += 1
                continue
            reason = line._get_skip_reason(splits)
            if reason:
                skipped.append((alert.name, reason))
                continue
            try:
                with self.env.cr.savepoint():
                    if do_scrap:
                        alert._create_draft_scrap(line.scrap_qty)
                    if do_downgrade:
                        alert._create_draft_conversion(
                            line.downgrade_qty, line.result_product_id, splits._get_results())
                    if assignment:
                        alert.write(assignment)
            except UserError as error:
                skipped.append((alert.name, error.args[0]))
                continue
            counts["scraps"] += do_scrap
            counts["conversions"] += do_downgrade
        message = _(
            "Drafts created: %(scraps)s Scrap(s) and %(conversions)s Simplified MO(s). "
            "Check them and validate them to move the stock.", **counts)
        if counts["untouched"]:
            message += _(" %s alert(s) without a quantity were left as they are.", counts["untouched"])
        return self.env["qc.alert"]._bulk_result_action(_("Execute Alerts"), message, skipped)


class QcAlertExecuteWizardLine(models.TransientModel):
    _name = "qc.alert.execute.wizard.line"
    _description = "Execute Quality Alert Line"

    wizard_id = fields.Many2one("qc.alert.execute.wizard", required=True, ondelete="cascade")
    alert_id = fields.Many2one("qc.alert", required=True, readonly=True)
    product_id = fields.Many2one(related="alert_id.product_id")
    lot_id = fields.Many2one(related="alert_id.lot_id")
    scrap_proposed_qty = fields.Float(related="alert_id.scrap_proposed_qty")
    scrap_qty = fields.Float(digits=(16, 2))
    downgrade_proposed_qty = fields.Float(related="alert_id.downgrade_proposed_qty")
    downgrade_qty = fields.Float(digits=(16, 2))
    result_product_id = fields.Many2one(
        "product.product", string="Result Product", domain=[("type", "=", "product")],
        help="Only for the downgrade: the product the Simplified MO makes, with the Downgrade Qty. "
             "A Scrap makes no product. To make several products, use the Split Result table instead.")
    has_scrap = fields.Boolean(compute="_compute_documents")
    has_conversion = fields.Boolean(compute="_compute_documents")
    note = fields.Char(compute="_compute_documents")

    @api.depends("alert_id.scrap_ids.state", "alert_id.conversion_ids.state")
    def _compute_documents(self):
        for line in self:
            scrap = line.alert_id.scrap_ids[:1]
            conversion = line.alert_id._active_conversion() if line.alert_id else self.env["qc.conversion"]
            line.has_scrap = bool(scrap)
            line.has_conversion = bool(conversion)
            notes = []
            if scrap:
                notes.append(_("Scrap made (%s)", line._state_label(scrap)))
            if conversion:
                notes.append(_("%(smo)s made (%(state)s)", smo=conversion.reference, state=line._state_label(conversion)))
            line.note = ", ".join(notes)

    @staticmethod
    def _state_label(record):
        return dict(record._fields["state"]._description_selection(record.env)).get(record.state, record.state)

    def _get_skip_reason(self, splits=None):
        """Why this line cannot get its drafts; False when it can. splits: its Split Result rows."""
        self.ensure_one()
        alert = self.alert_id
        do_scrap, do_downgrade = self.scrap_qty > 0, self.downgrade_qty > 0
        if not alert.product_id:
            return _("no product")
        if do_scrap and self.has_scrap:
            return _("already has a Scrap")
        if splits and not do_downgrade:
            return _("has Split Result rows but no Downgrade Qty")
        if do_downgrade and self.has_conversion:
            return _("already has a draft Simplified MO")
        if do_downgrade and splits:
            return splits._get_skip_reason(self.downgrade_qty)
        if do_downgrade and not self.result_product_id:
            return _("no Result Product")
        if do_downgrade and self.result_product_id == alert.product_id:
            return _("the Result Product is the alert's own product")
        return False


class QcAlertExecuteWizardSplit(models.TransientModel):
    """One result of an alert whose Downgrade Qty is split: another product, or a piece of the same one."""

    _name = "qc.alert.execute.wizard.split"
    _description = "Execute Quality Alert Split Result"

    wizard_id = fields.Many2one("qc.alert.execute.wizard", required=True, ondelete="cascade")
    alert_id = fields.Many2one("qc.alert", string="Alert", required=True)
    product_id = fields.Many2one(
        "product.product", string="Result Product", required=True, domain=[("type", "=", "product")])
    qty = fields.Float(string="Qty", digits=(16, 2), required=True)
    product_tracking = fields.Selection(related="product_id.tracking")
    lot_name = fields.Char(
        string="Lot",
        help="Required when the product is tracked by lot. This lot is used, or made when it does not "
             "exist yet.")

    @api.onchange("alert_id")
    def _onchange_alert_id(self):
        """Fill an empty Qty with the part of the alert's Downgrade Qty not split yet (it can still be changed)."""
        if not self.alert_id or self.qty:
            return
        wizard = self.wizard_id
        line = wizard.line_ids.filtered(lambda l: l.alert_id == self.alert_id)[:1]
        others = wizard.split_ids.filtered(lambda s: s.alert_id == self.alert_id)
        self.qty = max(line.downgrade_qty - sum(others.mapped("qty")), 0.0)

    def _get_skip_reason(self, downgrade_qty):
        """Why these rows (all of one alert) cannot make the Simplified MO; False when they can."""
        alert = self.alert_id
        if any(split.qty <= 0 for split in self):
            return _("a Split Result row has no qty")
        if any(split.product_tracking != "none" and not (split.lot_name or "").strip() for split in self):
            return _("a Split Result row has no Lot (its product is tracked by lot)")
        # The total of the rows is free: it may be lower or higher than the Downgrade Qty (the Downgrade Qty
        # is what is consumed from the lot).
        same = self.filtered(lambda split: split.product_id == alert.product_id)
        if same == self and len(self) == 1 and (self.lot_name or "").strip() in ("", alert.lot_id.name):
            return _("nothing changes: one row with the alert's own product and lot")
        used = [(split.product_id, (split.lot_name or "").strip()) for split in self if (split.lot_name or "").strip()]
        if len(used) != len(set(used)):
            return _("two Split Result rows use the same product and lot")
        return False

    def _get_results(self):
        """(product, qty, lot) per row, all rows of one alert. Lots of the same product as the alert are
        decided here; other rows without a lot get the alert's lot name later (Generate Lot)."""
        alert = self.alert_id
        source = alert.lot_id
        Lot = self.env["stock.lot"].with_context(clean_context(self.env.context))
        company = alert.company_id or self.env.company
        same = self.filtered(lambda split: source and split.product_id == alert.product_id
                             and split.product_id.tracking != "none")
        keeper_taken = any((split.lot_name or "").strip() == source.name for split in same)
        taken = {(split.product_id.id, (split.lot_name or "").strip()) for split in self}
        new_lots = {}
        for split in same.filtered(lambda s: not (s.lot_name or "").strip()).sorted(lambda s: -s.qty):
            if not keeper_taken:
                new_lots[split.id] = source
                keeper_taken = True
                continue
            number = 2
            while ((split.product_id.id, "%s-%s" % (source.name, number)) in taken
                   or Lot.search_count([("name", "=", "%s-%s" % (source.name, number)),
                                        ("product_id", "=", split.product_id.id),
                                        ("company_id", "=", company.id)])):
                number += 1
            name = "%s-%s" % (source.name, number)
            taken.add((split.product_id.id, name))
            new_lots[split.id] = Lot.create({"name": name, "product_id": split.product_id.id,
                                             "company_id": company.id})
        results = []
        for split in self:
            name = (split.lot_name or "").strip()
            lot = new_lots.get(split.id, Lot)
            if name and split.product_id.tracking != "none":
                lot = Lot.search([("name", "=", name), ("product_id", "=", split.product_id.id),
                                  ("company_id", "=", company.id)], limit=1) or Lot.create(
                    {"name": name, "product_id": split.product_id.id, "company_id": company.id})
            results.append((split.product_id, split.qty, lot))
        return results
