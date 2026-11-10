from collections import defaultdict
from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models

GROUPS = [
    ("received", "Diterima"),
    ("pending", "Belum diterima"),
    ("late_received", "Tertunda, diterima hari ini"),
    ("late", "Terlambat, belum diterima"),
]
AGES = [
    ("today", "Hari ini"),
    ("1_3", "1-3 hari"),
    ("4_7", "4-7 hari"),
    ("8_14", "8-14 hari"),
    ("15", "> 14 hari"),
]


class ReceiptMonitorLine(models.TransientModel):
    """One WH/IN of the Monitor Penerimaan for one date. Made when a group is opened from the monitor,
    so the list can be grouped by Partner and Umur and exported."""

    _name = "mst.receipt.monitor.line"
    _description = "Monitor Penerimaan Line"
    _order = "age_days desc, scheduled_date, id"

    monitor_date = fields.Date(string="Tanggal Monitor", readonly=True)
    group = fields.Selection(GROUPS, string="Kelompok", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Dokumen", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Partner", readonly=True)
    origin = fields.Char(string="Origin (PO)", readonly=True)
    location_id = fields.Many2one("stock.location", string="Dari", readonly=True)
    location_dest_id = fields.Many2one("stock.location", string="Ke", readonly=True)
    scheduled_date = fields.Datetime(string="Jadwal", readonly=True)
    date_done = fields.Datetime(string="Tanggal Terima", readonly=True)
    age_days = fields.Integer(string="Umur (hari)", readonly=True)
    age = fields.Selection(AGES, string="Umur", readonly=True)
    state = fields.Selection(related="picking_id.state", string="Status")
    yds = fields.Float(string="yds", digits=(16, 2), readonly=True)
    kg = fields.Float(string="kg", digits=(16, 2), readonly=True)
    roll = fields.Integer(string="Roll", readonly=True)

    # ---------------------------------------------------------------- dates
    @api.model
    def _get_tz(self):
        return pytz.timezone(self.env.user.tz or "Asia/Jakarta")

    @api.model
    def _get_day(self, date_str=False):
        """The day to show (default today in the user's time zone, WIB) and its bounds in UTC."""
        tz = self._get_tz()
        day = fields.Date.to_date(date_str) if date_str else datetime.now(tz).date()
        start = tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)
        return day, start, start + timedelta(days=1)

    @api.model
    def _local_date(self, value):
        return pytz.utc.localize(value).astimezone(self._get_tz()).date()

    @api.model
    def _get_age(self, day, scheduled_date):
        days = max((day - self._local_date(scheduled_date)).days, 0)
        if days == 0:
            key = "today"
        elif days <= 3:
            key = "1_3"
        elif days <= 7:
            key = "4_7"
        elif days <= 14:
            key = "8_14"
        else:
            key = "15"
        return days, key

    # ------------------------------------------------------------ the rules
    @api.model
    def _get_pickings(self, start, end, steps=False):
        """WH/IN that can be in one of the four groups of the day (Receipts of the chosen scope, not draft
        or cancel)."""
        return self.env["stock.picking"].search([
            ("picking_type_id", "in", self._get_scope_types(steps).ids),
            ("state", "not in", ("draft", "cancel")),
            ("scheduled_date", "<", end),
            "|", ("state", "!=", "done"),
            "|", ("scheduled_date", ">=", start), ("date_done", ">=", start),
        ])

    @api.model
    def _get_group(self, picking, start, end):
        """Diterima: scheduled that day and done. Belum diterima: scheduled that day, not done.
        Tertunda, diterima hari ini: scheduled before, done that day. Terlambat: scheduled before, not done."""
        done = picking.state == "done"
        if start <= picking.scheduled_date < end:
            return "received" if done else "pending"
        if not done:
            return "late"
        if picking.date_done and start <= picking.date_done < end:
            return "late_received"
        return False

    @api.model
    def _get_units(self, pickings):
        """yds, kg and roll per picking. Lengths are turned into yards (m x 1.0936), weights into kg;
        other units are not added. Roll = move lines with a lot."""
        units = defaultdict(lambda: [0.0, 0.0, 0])
        yard = self.env.ref("uom.product_uom_yard")
        kg = self.env.ref("uom.product_uom_kgm")
        moves = self.env["stock.move"].search([("picking_id", "in", pickings.ids), ("state", "!=", "cancel")])
        for move in moves:
            qty = move.quantity if move.state == "done" else move.product_uom_qty
            uom = move.product_uom
            if uom.category_id == yard.category_id:
                units[move.picking_id.id][0] += uom._compute_quantity(qty, yard, round=False)
            elif uom.category_id == kg.category_id:
                units[move.picking_id.id][1] += uom._compute_quantity(qty, kg, round=False)
        rolls = self.env["stock.move.line"]._read_group(
            [("picking_id", "in", pickings.ids), "|", ("lot_id", "!=", False), ("lot_name", "!=", False)],
            ["picking_id"], ["__count"])
        for picking, count in rolls:
            units[picking.id][2] = count
        return units

    @api.model
    def _get_rows(self, date_str=False, steps=False):
        day, start, end = self._get_day(date_str)
        pickings = self._get_pickings(start, end, steps)
        units = self._get_units(pickings)
        rows = []
        for picking in pickings:
            group = self._get_group(picking, start, end)
            if not group:
                continue
            age_days, age = self._get_age(day, picking.scheduled_date)
            yds, kg, roll = units[picking.id]
            rows.append({
                "monitor_date": day,
                "group": group,
                "picking_id": picking.id,
                "partner_id": picking.partner_id.id,
                "origin": picking.origin,
                "location_id": picking.location_id.id,
                "location_dest_id": picking.location_dest_id.id,
                "scheduled_date": picking.scheduled_date,
                "date_done": picking.date_done,
                "age_days": age_days,
                "age": age,
                "yds": yds,
                "kg": kg,
                "roll": roll,
            })
        return day, rows

    # ------------------------------------------------------- for the screen
    @api.model
    def _get_step_kind(self, picking_type):
        """The number of steps of the warehouse of a Receipts type, as text: '1', '2' or '3'
        (the reception_steps of the warehouse: one_step, two_steps, three_steps)."""
        steps = {"one_step": "1", "two_steps": "2", "three_steps": "3"}
        return steps.get(picking_type.warehouse_id.reception_steps, "2")

    @api.model
    def _get_scope_types(self, steps=False):
        """The Receipts types in the scope. steps = the kinds chosen on the screen ('1', '2', '3'); empty = all."""
        types = self.env["stock.picking.type"].search([("code", "=", "incoming")])
        if steps:
            types = types.filtered(lambda t: self._get_step_kind(t) in steps)
        return types

    @api.model
    def _get_scope_options(self, steps=False):
        """The kinds of Receipts that exist, for the Cakupan of the screen, with the chosen ones marked:
        'Receipts (3 langkah)', 'Receipts (2 langkah)', 'Receipts (1 langkah)'."""
        kinds = sorted({self._get_step_kind(t) for t in self._get_scope_types()}, reverse=True)
        chosen = [k for k in kinds if not steps or k in steps] or kinds
        return [{"key": k, "label": "Receipts (%s langkah)" % k, "selected": k in chosen} for k in kinds]

    @api.model
    def get_monitor_data(self, date_str=False, steps=False):
        """Summary for the Monitor Penerimaan screen: the four groups and the age of the late ones."""
        options = self._get_scope_options(steps)
        steps = [o["key"] for o in options if o["selected"]]
        day, rows = self._get_rows(date_str, steps)
        groups = {key: {"key": key, "label": label, "doc": 0, "yds": 0.0, "kg": 0.0, "roll": 0}
                  for key, label in GROUPS}
        ages = {key: {"key": key, "label": label, "doc": 0, "yds": 0.0} for key, label in AGES if key != "today"}
        for row in rows:
            total = groups[row["group"]]
            total["doc"] += 1
            total["yds"] += row["yds"]
            total["kg"] += row["kg"]
            total["roll"] += row["roll"]
            if row["group"] == "late" and row["age"] in ages:
                ages[row["age"]]["doc"] += 1
                ages[row["age"]]["yds"] += row["yds"]
        return {
            "date": fields.Date.to_string(day),
            "scope_options": options,
            "updated": datetime.now(self._get_tz()).strftime("%H:%M"),
            "groups": [groups[key] for key, _label in GROUPS],
            "ages": list(ages.values()),
            # The WH/IN of the day too, so the screen needs one call only (the rows are computed once).
            "rows": self._serialize_rows(rows),
        }

    @api.model
    def _filter_rows(self, rows, group=False, age=False, search=False):
        """Rows of one group and one age (empty = all), whose Dokumen, Partner or Origin has the search text."""
        rows = [row for row in rows if (not group or row["group"] == group) and (not age or row["age"] == age)]
        if search and search.strip():
            text = search.strip().lower()
            pickings = {p.id: p for p in self.env["stock.picking"].browse([row["picking_id"] for row in rows])}
            rows = [row for row in rows if text in (
                "%s %s %s" % (pickings[row["picking_id"]].name, pickings[row["picking_id"]].partner_id.display_name or "",
                              row["origin"] or "")).lower()]
        return rows

    @api.model
    def get_group_rows(self, date_str, group=False, age=False, steps=False):
        """WH/IN of the day for the list under the summary, oldest first. An empty group means all groups."""
        _day, rows = self._get_rows(date_str, steps)
        return self._serialize_rows(self._filter_rows(rows, group, age))

    @api.model
    def _serialize_rows(self, rows):
        """Rows for the list under the summary, oldest first, with the texts the screen shows."""
        rows = sorted(rows, key=lambda row: (-row["age_days"], row["scheduled_date"] or datetime.min))
        pickings = self.env["stock.picking"].browse([row["picking_id"] for row in rows])
        by_id = {picking.id: picking for picking in pickings}
        states = dict(self.env["stock.picking"]._fields["state"]._description_selection(self.env))
        ages = dict(AGES)
        months = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
        result = []
        for row in rows:
            picking = by_id[row["picking_id"]]
            local = self._local_date(row["scheduled_date"])
            result.append({
                "id": picking.id,
                "name": picking.name,
                "partner": picking.partner_id.display_name or "-",
                "origin": row["origin"] or "",
                "source": picking.location_id.display_name,
                "dest": picking.location_dest_id.display_name,
                "scheduled": "%s %s" % (local.day, months[local.month - 1]),
                "group": row["group"],
                "group_label": dict(GROUPS)[row["group"]],
                "age": row["age"],
                "age_label": ages[row["age"]],
                "state": states.get(picking.state, picking.state),
                "yds": row["yds"],
                "kg": row["kg"],
            })
        return result

    @api.model
    def action_open_group(self, date_str, group=False, age=False, group_by="partner", search=False, steps=False):
        """The list of WH/IN with the filters of the screen, in a list view (group by and Export)."""
        day, rows = self._get_rows(date_str, steps)
        rows = self._filter_rows(rows, group, age, search)
        lines = self.create(rows)
        label = dict(GROUPS)[group] if group else "Semua penerimaan"
        if age:
            label = "%s · %s" % (label, dict(AGES)[age])
        by = group_by if group_by in ("partner", "age", "group", "dest") else False
        return {
            "type": "ir.actions.act_window",
            "name": "%s · %s" % (label, day.strftime("%d/%m/%Y")),
            "res_model": self._name,
            "view_mode": "tree",
            "views": [(self.env.ref("mst17_receipt_monitor.receipt_monitor_line_view_tree").id, "list")],
            "search_view_id": self.env.ref("mst17_receipt_monitor.receipt_monitor_line_view_search").id,
            "domain": [("id", "in", lines.ids)],
            "context": {"search_default_group_%s" % by: 1} if by else {},
        }

    def action_open_picking(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Receipt"),
            "res_model": "stock.picking",
            "view_mode": "form",
            "views": [(False, "form")],
            "res_id": self.picking_id.id,
        }
