from odoo import _, fields, models
from odoo.exceptions import UserError

RECEIVING_STATUS = [
    ("to_store", "To Store"),
    ("qc_request", "QC Request"),
    ("ready_for_qc", "Ready for QC"),
    ("done", "Done"),
]
QC_REQUEST_ALLOWED = ("qc_request", "ready_for_qc")


class StockMoveLine(models.Model):
    _inherit = "stock.move.line"

    assignee_id = fields.Many2one(
        "res.users", string="Assignee", copy=False,
        domain=lambda self: [("groups_id", "in", self.env.ref("stock.group_stock_user").ids)],
        help="User inventory yang ditugaskan menyimpan barang.",
    )
    receiving_status = fields.Selection(RECEIVING_STATUS, string="Receiving Status", copy=False)
    qc_request_status = fields.Selection(
        [("requested", "QC Requested")], string="QC Request Status", readonly=True, copy=False,
    )
    qc_check_ids = fields.One2many("qc.check", "move_line_id", string="Quality Checks")

    def _get_qc_label(self):
        self.ensure_one()
        lot = self.lot_id.name or self.lot_name or ""
        return f"{self.product_id.display_name} {lot}".strip()

    def _get_on_demand_qc_point(self):
        self.ensure_one()
        points = self.env["qc.point"]._get_points(self.picking_id.picking_type_id, "on_demand", self.company_id)
        return points.filtered(lambda p: p._applies_to_product(self.product_id))[:1]

    def _prepare_qc_check_vals(self, point):
        self.ensure_one()
        return {
            "picking_id": self.picking_id.id,
            "move_line_id": self.id,
            "product_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "lot_name": self.lot_id.name or self.lot_name,
            "qty_to_test": self.quantity,
            "yard_asal": self.quantity,
            "point_id": point.id,
            "team_id": point._get_team().id,
            "user_id": point.user_id.id or self.env.user.id,
            "test_type": point.test_type,
            "company_id": self.company_id.id,
        }

    def _can_request_qc(self, point):
        self.ensure_one()
        return len(self.qc_check_ids) < max(point.max_tests, 1)

    def _create_qc_check(self, point):
        self.ensure_one()
        check = self.env["qc.check"].sudo().create(self._prepare_qc_check_vals(point))
        self.qc_request_status = "requested"
        return check

    def action_request_qc(self):
        checks = self.env["qc.check"]
        skipped = {"status": [], "point": [], "max_tests": []}
        for line in self:
            if line.state in ("done", "cancel") or line.receiving_status not in QC_REQUEST_ALLOWED:
                skipped["status"].append(line._get_qc_label())
                continue
            point = line._get_on_demand_qc_point()
            if not point:
                skipped["point"].append(line._get_qc_label())
                continue
            if not line._can_request_qc(point):
                skipped["max_tests"].append(line._get_qc_label())
                continue
            checks |= line._create_qc_check(point)

        if not checks:
            raise UserError(self._format_request_qc_error(skipped))
        if any(skipped.values()):
            self._notify_skipped_lines(sum(len(labels) for labels in skipped.values()))
        return self._action_open_qc_checks(checks)

    def _format_request_qc_error(self, skipped):
        reasons = {
            "status": _("Receiving Status harus 'QC Request' atau 'Ready for QC':"),
            "point": _("Tidak ada QC Point 'On Demand' untuk:"),
            "max_tests": _("Sudah mencapai Maximum Number of Tests:"),
        }
        parts = [_("Tidak ada QC Form yang terbentuk.")]
        for key, labels in skipped.items():
            if labels:
                parts.append("%s\n- %s" % (reasons[key], "\n- ".join(labels)))
        return "\n\n".join(parts)

    def _notify_skipped_lines(self, count):
        self.env["bus.bus"]._sendone(self.env.user.partner_id, "simple_notification", {
            "type": "warning",
            "title": _("Sebagian baris dilewati"),
            "message": _("%s baris tidak dibuatkan QC Form.", count),
        })

    def _action_open_qc_checks(self, checks):
        # After Request QC, open every Quality Check of the same transfer(s), not only the new records.
        action = self.env["ir.actions.act_window"]._for_xml_id("quality_control.qc_check_action")
        action["domain"] = [("picking_id", "in", checks.picking_id.ids)]
        action["context"] = {}
        return action
