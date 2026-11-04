from odoo import _, api, fields, models
from odoo.exceptions import UserError

from .stock_move_line import QC_REQUEST_ALLOWED

QC_STATUS = [
    ("no_qc", "No QC"),
    ("pending", "Pending QC"),
    ("failed", "Failed QC"),
    ("partial", "Partially Pass"),
    ("passed", "Passed QC"),
]


class StockPicking(models.Model):
    _inherit = "stock.picking"

    qc_check_ids = fields.One2many("qc.check", "picking_id", string="Quality Checks")
    qc_alert_ids = fields.One2many("qc.alert", "picking_id", string="Quality Alerts")
    qc_check_count = fields.Integer(compute="_compute_qc_counts")
    qc_todo_count = fields.Integer(compute="_compute_qc_counts")
    qc_alert_count = fields.Integer(compute="_compute_qc_counts")
    has_qc = fields.Boolean(string="Has QC", compute="_compute_qc_status", store=True)
    has_on_demand_qc = fields.Boolean(compute="_compute_has_on_demand_qc")
    has_failed_qc = fields.Boolean(compute="_compute_has_failed_qc")
    qc_status = fields.Selection(QC_STATUS, string="QC Status", compute="_compute_qc_status", store=True)

    @api.depends("qc_check_ids", "qc_alert_ids")
    def _compute_qc_counts(self):
        for picking in self:
            picking.qc_check_count = len(picking.qc_check_ids)
            picking.qc_todo_count = len(picking.qc_check_ids.filtered(lambda c: c.quality_state == "todo"))
            picking.qc_alert_count = len(picking.qc_alert_ids)

    @api.depends("qc_check_ids.quality_state")
    def _compute_has_failed_qc(self):
        for picking in self:
            picking.has_failed_qc = any(c.quality_state == "fail" for c in picking.qc_check_ids)

    def _compute_has_on_demand_qc(self):
        for picking in self:
            picking.has_on_demand_qc = (
                picking.state not in ("done", "cancel") and picking._has_on_demand_qc_point()
            )

    @api.depends("qc_check_ids.quality_state")
    def _compute_qc_status(self):
        for picking in self:
            states = set(picking.qc_check_ids.mapped("quality_state"))
            picking.has_qc = bool(states)
            if not states:
                picking.qc_status = "no_qc"
            elif states == {"todo"}:
                picking.qc_status = "pending"
            elif states == {"pass"}:
                picking.qc_status = "passed"
            elif states == {"fail"}:
                picking.qc_status = "failed"
            elif "fail" in states:
                picking.qc_status = "partial"
            else:
                picking.qc_status = "pending"

    def _has_on_demand_qc_point(self):
        self.ensure_one()
        return bool(self.env["qc.point"]._get_points(self.picking_type_id, "on_demand"))

    def _get_qc_partner(self):
        """Follow-up transfers (e.g. QC Process) have no partner: take the vendor of the origin receipt."""
        partner = self.partner_id[:1]
        moves = self.move_ids
        while not partner and moves:
            moves = moves.move_orig_ids
            partner = moves.picking_id.partner_id[:1]
        return partner

    def _is_second_step_receipt(self):
        self.ensure_one()
        input_location = self.picking_type_id.warehouse_id.wh_input_stock_loc_id
        return bool(
            self.picking_type_id.code == "internal"
            and input_location
            and self.location_id._child_of(input_location)
        )

    def _action_open_related(self, xmlid):
        self.ensure_one()
        action = self.env["ir.actions.act_window"]._for_xml_id(xmlid)
        action["domain"] = [("picking_id", "=", self.id)]
        action["context"] = {"default_picking_id": self.id}
        return action

    def _create_qc_checks(self, moves=None):
        checks = self.env["qc.check"].sudo()
        vals_list = []
        for picking in self:
            points = self.env["qc.point"]._get_points(picking.picking_type_id, "all", picking.company_id)
            if not points:
                continue
            todo_moves = (picking.move_ids if moves is None else moves).filtered(
                lambda m: m.state not in ("cancel", "done"))
            existing = {(c.point_id.id, c.product_id.id) for c in picking.qc_check_ids.sudo() if c.point_id}
            for move in todo_moves:
                for point in points:
                    key = (point.id, move.product_id.id)
                    if key in existing or not point._applies_to_product(move.product_id):
                        continue
                    existing.add(key)
                    vals_list.append(picking._prepare_qc_check_vals(move, point))
        return checks.create(vals_list) if vals_list else checks

    def _prepare_qc_check_vals(self, move, point):
        self.ensure_one()
        move_line = move.move_line_ids[:1]
        qty_to_test = move.product_uom_qty * point.partial_test / 100.0
        return {
            "picking_id": self.id,
            "move_line_id": move_line.id,
            "lot_id": move_line.lot_id.id,
            "lot_name": move_line.lot_name or move_line.lot_id.name,
            "product_id": move.product_id.id,
            "point_id": point.id,
            "team_id": point._get_team().id,
            "user_id": point.user_id.id or self.env.user.id,
            "yard_asal": qty_to_test,  # TSD: Yard Asal = Qty to Test
            "test_type": point.test_type,
            "qty_to_test": qty_to_test,
            "company_id": self.company_id.id,
        }

    def button_validate(self):
        if not self.env.context.get("skip_qc"):
            for picking in self:
                picking._check_receiving_status_done()
                picking._check_mandatory_qc_done()
        return super().button_validate()

    def _check_receiving_status_done(self):
        self.ensure_one()
        if not (self._is_second_step_receipt() and self._has_on_demand_qc_point()):
            return
        not_done = self.move_line_ids.filtered(
            lambda line: line.receiving_status not in ("done", "ready_for_qc"))
        if not_done:
            raise UserError(_(
                "Please make sure that Receiving Status of all products are \"Done\" or "
                "\"Ready for QC\" prior to validating this form.\n\n%s",
                "\n".join("- %s" % line._get_qc_label() for line in not_done),
            ))

    def _check_mandatory_qc_done(self):
        self.ensure_one()
        pending = self.qc_check_ids.filtered(
            lambda check: check.quality_state == "todo" and check.point_id.is_mandatory)
        if pending:
            raise UserError(_(
                "Product(s) Quality Control Still Pending.\n"
                "Selesaikan Quality Check berikut sebelum memvalidasi %(picking)s:\n%(lines)s",
                picking=self.name,
                lines="\n".join("- %s: %s" % (c.name, c.product_id.display_name) for c in pending),
            ))

    def action_view_qc_checks(self):
        return self._action_open_related("quality_control.qc_check_action")

    def action_view_qc_alerts(self):
        return self._action_open_related("quality_control.qc_alert_action")

    def open_quality_alert(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Quality Alerts"),
            "res_model": "qc.alert.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {"default_picking_id": self.id},
        }

    def _action_open_alerts(self, alerts):
        action = self._action_open_related("quality_control.qc_alert_action")
        action["domain"] = [("id", "in", alerts.ids)]
        if len(alerts) == 1:
            action["views"] = [(False, "form")]
            action["res_id"] = alerts.id
        return action

    def action_request_qc(self):
        self.ensure_one()
        lines = self.move_line_ids.filtered(lambda line: line.receiving_status in QC_REQUEST_ALLOWED)
        if not lines:
            raise UserError(_(
                "Belum ada baris Detailed Operations dengan Receiving Status 'QC Request' atau "
                "'Ready for QC'. Isi Receiving Status di Detailed Operations terlebih dahulu."))
        return lines.action_request_qc()
