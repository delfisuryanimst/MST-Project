import re
from collections import defaultdict
from datetime import datetime, timedelta

from odoo import _, api, fields, models
from odoo.tools import html2plaintext

from odoo.addons.mst17_receipt_monitor.models.receipt_monitor import AGES

PERIODS = [("today", "Hari ini"), ("late", "Tertunda"), ("d1", "H+1"), ("d2", "H+2"), ("d3", "H+3")]
SCOPES = [("operational", "Operasional"), ("sample", "Sample"), ("all", "Semua")]
STATUSES = [("waiting", "Waiting"), ("ready", "Ready"), ("qty_real", "Qty Real Ready (CBD)"), ("done", "Done")]
KINDS = [("customer", "Customer (WH/OUT)"), ("sample", "Sample (WH/OUT)"), ("subcon", "Subcon (WH/RES)"),
         ("agent", "Agen MTX (WH/MTX)")]
CATEGORIES = [("denim", "Denim & BBSF"), ("greige", "Greige"), ("non_denim", "Non-Denim")]
# Ordered from light to heavy: the issue of a document is the heaviest of its lines.
ISSUES = [("available", "Stok tersedia, belum disiapkan"), ("qc", "Stok di QC0500"),
          ("short", "Stok belum cukup"), ("none", "Stok di MST tidak tersedia")]
ISSUE_RANK = {key: rank for rank, (key, _label) in enumerate(ISSUES)}
SAMPLE_MAX_YDS = 100
SAMPLE_NOTE = re.compile(r"sampe?l", re.I)
# Notes that mention a sample without the delivery being one (FDD R-05).
NOT_SAMPLE_NOTE = re.compile(
    r"shipping\s+sampe?l|tunggu.{0,40}sampe?l|titip\w*\s+sampe?l|ada\s+sampe?l|sampe?l\w*\s+di\s+ppic"
    r"|ke\s+sampe?l|retur\s+adm", re.I)


def _num(value):
    """1234.5 -> '1.234,5' (Indonesian format, no trailing zero)."""
    text = "{:,.1f}".format(value)
    text = text[:-2] if text.endswith(".0") else text
    return text.translate({ord(","): ".", ord("."): ","})


def _qty(unit, value):
    return _num(value) if unit == "yds" else "%s %s" % (_num(value), unit)


class DeliveryMonitorLine(models.TransientModel):
    """One delivery document of the Monitor Pengiriman, made when a row of the summary is opened,
    so the list can be grouped and exported."""

    _name = "mst.delivery.monitor.line"
    _description = "Monitor Pengiriman Line"
    _order = "age_days desc, scheduled_date, id"
    _rec_name = "picking_id"

    monitor_date = fields.Date(string="Tanggal Monitor", readonly=True)
    period = fields.Selection(PERIODS, string="Periode", readonly=True)
    picking_id = fields.Many2one("stock.picking", string="Dokumen", readonly=True)
    partner_id = fields.Many2one("res.partner", string="Partner", readonly=True)
    origin = fields.Char(string="Origin", readonly=True)
    kind = fields.Selection(KINDS, string="Tipe Kiriman", readonly=True)
    category = fields.Selection(CATEGORIES, string="Kategori", readonly=True)
    scheduled_date = fields.Datetime(string="Jadwal", readonly=True)
    date_done = fields.Datetime(string="Selesai", readonly=True)
    status = fields.Selection(STATUSES, string="Status", readonly=True)
    issue = fields.Selection(ISSUES, string="Kendala", readonly=True)
    late_done = fields.Boolean(string="Tertunda, selesai hari ini", readonly=True)
    age_days = fields.Integer(string="Umur (hari)", readonly=True)
    age = fields.Selection(AGES, string="Umur", readonly=True)
    yds = fields.Float(string="yds", digits=(16, 2), readonly=True)
    kg = fields.Float(string="kg", digits=(16, 2), readonly=True)
    roll = fields.Integer(string="Roll", readonly=True)
    free_stock = fields.Char(string="Stok bebas MST", readonly=True)
    note = fields.Text(string="Catatan", readonly=True)
    product_line_ids = fields.One2many("mst.delivery.monitor.product", "line_id", string="Rincian", readonly=True)

    # ------------------------------------------------------------ the rules
    @api.model
    def _get_pickings(self, period, start, end):
        """Hari ini: scheduled that day (plus the late ones done that day). Tertunda: scheduled before, not done.
        H+n: scheduled n days later, not done. WH/OUT and WH/RES only with a star unless done; WH/MTX all."""
        types = self.env["stock.picking.type"].sudo().search([("delivery_monitor_kind", "!=", False)])
        domain = [
            ("picking_type_id", "in", types.ids),
            ("state", "not in", ("draft", "cancel")),
            ("company_id", "in", self.env.companies.ids),
            "|", "|", ("picking_type_id.delivery_monitor_kind", "=", "agent"),
            ("priority", "=", "1"), ("state", "=", "done"),
        ]
        if period == "today":
            domain += [("scheduled_date", "<", end), "|", ("scheduled_date", ">=", start), ("date_done", ">=", start)]
        elif period == "late":
            domain += [("scheduled_date", "<", start), ("state", "!=", "done")]
        else:
            shift = timedelta(days=int(period[1]))
            domain += [("scheduled_date", ">=", start + shift), ("scheduled_date", "<", end + shift),
                       ("state", "!=", "done")]
        return self.env["stock.picking"].sudo().search(domain, order="scheduled_date, id")

    @api.model
    def _status(self, picking, note):
        if picking.state == "done":
            return "done"
        if picking.state == "assigned":
            return "qty_real" if "QTY REAL" in note.upper() else "ready"
        return "waiting"

    @api.model
    def _category_groups(self, categories):
        """Denim, Greige or Non-Denim per category: the nearest category (itself first) with a group."""
        result = {}
        for categ in categories:
            chain = self.env["product.category"].sudo().browse(
                [int(i) for i in reversed(categ.parent_path.split("/")[:-1])])
            result[categ.id] = next((c.delivery_monitor_group for c in chain if c.delivery_monitor_group), "non_denim")
        return result

    @api.model
    def _inspected_lots(self, lots):
        """Lots whose last QC line is Pass or Fail. Empty when the QC module of live is not installed."""
        if not lots or "sh.quality.check.line" not in self.env:
            return set()
        latest = {}
        for line in self.env["sh.quality.check.line"].sudo().search_read(
                [("lot_id", "in", lots.ids)], ["lot_id", "stage"], order="id desc"):
            latest.setdefault(line["lot_id"][0], line["stage"])
        return {lot for lot, stage in latest.items() if stage in ("pass", "fail")}

    @api.model
    def _get_rows(self, date_str=False, period="today", scope="all", category="all"):
        receipt = self.env["mst.receipt.monitor.line"]
        day, start, end = receipt._get_day(date_str)
        pickings = self._get_pickings(period, start, end)
        moves = self.env["stock.move"].sudo().search([("picking_id", "in", pickings.ids), ("state", "!=", "cancel")])
        move_lines = self.env["stock.move.line"].sudo().search([("picking_id", "in", pickings.ids)])
        yard, kg = self.env.ref("uom.product_uom_yard"), self.env.ref("uom.product_uom_kgm")
        Location = self.env["stock.location"].sudo()
        roles = {loc.id: loc.delivery_monitor_role for loc in Location.search([("delivery_monitor_role", "!=", False)])}
        groups = self._category_groups(moves.product_id.categ_id)

        def unit(uom, qty):
            if uom.category_id == yard.category_id:
                return "yds", uom._compute_quantity(qty, yard, round=False)
            if uom.category_id == kg.category_id:
                return "kg", uom._compute_quantity(qty, kg, round=False)
            return uom.name, qty

        def role(location):
            return next((roles[int(i)] for i in reversed(location.parent_path.split("/")[:-1]) if int(i) in roles), False)

        def sub(src, location):
            """The sub warehouse right under the source location (WH/Stock/L0100/A.R01 -> L0100)."""
            if not location.parent_path.startswith(src.parent_path):
                return False
            first = location.parent_path[len(src.parent_path):].split("/")[0]
            return Location.browse(int(first)).name if first else src.name

        lines_of, moves_of = defaultdict(list), defaultdict(list)
        for line in move_lines:
            lines_of[line.picking_id.id].append(line)
        for move in moves:
            moves_of[move.picking_id.id].append(move)

        notes = {p.id: html2plaintext(p.note or "") for p in pickings}
        statuses = {p.id: self._status(p, notes[p.id]) for p in pickings}
        stock = self._get_stock(moves.filtered(lambda m: statuses[m.picking_id.id] == "waiting").product_id, role)

        rows = []
        for picking in pickings:
            status = statuses[picking.id]
            scheduled_today = start <= picking.scheduled_date < end
            late_done = period == "today" and not scheduled_today
            if status == "done" and period == "today" and not (picking.date_done and start <= picking.date_done < end):
                continue
            kind = picking.picking_type_id.delivery_monitor_kind
            totals, per_product, by_category = defaultdict(float), defaultdict(lambda: [None, 0.0, 0.0]), defaultdict(float)
            for move in moves_of[picking.id]:
                name, value = unit(move.product_uom, move.quantity if move.state == "done" else move.product_uom_qty)
                totals[name] += value
                by_category[groups[move.product_id.categ_id.id]] += value
                entry = per_product[move.product_id]
                entry[0] = name
                entry[1] += value
                entry[2] += move.product_qty - move.product_uom._compute_quantity(
                    move.quantity, move.product_id.uom_id, round=False) if move.state != "done" else 0.0
            picking_lines = lines_of[picking.id]
            note = notes[picking.id]
            if kind == "customer" and per_product and all(
                    u == "yds" and q < SAMPLE_MAX_YDS for u, q, _need in per_product.values()) and (
                    (SAMPLE_NOTE.search(note) and not NOT_SAMPLE_NOTE.search(note))
                    or (picking_lines and all(role(l.location_id) == "sample" for l in picking_lines))):
                kind = "sample"
            row_category = max(by_category, key=by_category.get) if by_category else "non_denim"
            if (scope == "operational" and kind == "sample") or (scope == "sample" and kind != "sample") \
                    or (category != "all" and row_category != category):
                continue

            products, issue, free, qc_in, qc_out = [], False, defaultdict(float), 0.0, 0.0
            for product, (name, value, need) in per_product.items():
                product_line = {"product_id": product.id, "category": groups[product.categ_id.id],
                                "qty_text": _qty(name, value)}
                if status == "waiting":
                    info = self._line_stock(stock[product.id], picking.location_id, need, role, sub)
                    issue = max(issue or info["issue"], info["issue"], key=ISSUE_RANK.get)
                    for place, qty in info["mst"].items():
                        place_unit, place_qty = unit(product.uom_id, qty)
                        free[place, place_unit] += place_qty
                    inspected, open_qty = unit(product.uom_id, info["qc_in"])[1], unit(product.uom_id, info["qc_out"])[1]
                    if name == "yds":
                        qc_in += inspected
                        qc_out += open_qty
                    product_line.update(
                        issue=info["issue"],
                        free_stock=" · ".join("%s %s" % (p, _qty(*unit(product.uom_id, q)))
                                              for p, q in sorted(info["mst"].items())),
                        qc_stock="%s (sudah inspect %s · belum %s)" % (
                            _qty(*unit(product.uom_id, info["qc"])), _num(inspected), _num(open_qty)) if info["qc"] else False,
                        agent_stock=_qty(*unit(product.uom_id, info["agent"]))
                        if kind in ("customer", "sample") and info["agent"] else False,
                    )
                products.append(product_line)

            free_text = " · ".join("%s %s" % (place, _qty(u, q)) for (place, u), q in sorted(free.items()))
            if issue and not free_text and qc_in + qc_out:
                free_text = "QC0500 %s (sudah inspect %s · belum %s)" % (_num(qc_in + qc_out), _num(qc_in), _num(qc_out))
            elif issue == "none" and any(p.get("agent_stock") for p in products):
                free_text = "MTX %s · koordinasi Agen/Kemitraan" % " · ".join(p["agent_stock"] for p in products
                                                                              if p.get("agent_stock"))
            subs = defaultdict(int)
            if status != "waiting":
                for line in picking_lines:
                    place = role(line.location_id) != "excluded" and sub(picking.location_id, line.location_id)
                    if place:
                        subs[place] += 1 if (line.lot_id or line.lot_name) else 0
            age_days, age = receipt._get_age(day, picking.scheduled_date)
            rows.append({
                "picking": picking,
                "kind": kind,
                "category": row_category,
                "status": status,
                "issue": issue,
                "late_done": late_done,
                "age_days": age_days,
                "age": age,
                "yds": totals["yds"],
                "kg": totals["kg"],
                "roll": sum(1 for line in picking_lines if line.lot_id or line.lot_name),
                "free_stock": free_text,
                "qc_in": qc_in,
                "qc_out": qc_out,
                "subs": dict(subs),
                "note": note,
                "products": products,
            })
        return day, rows

    @api.model
    def _get_stock(self, products, role):
        """Free stock (quantity - reserved; booked rolls are reserved) per product and location, and the QC stock
        split in inspected or not, in the product unit."""
        stock = defaultdict(lambda: {"places": [], "qc_in": 0.0, "qc_out": 0.0})
        if not products:
            return stock
        Quant = self.env["stock.quant"].sudo()
        domain = [("product_id", "in", products.ids), ("location_id.usage", "=", "internal")]
        for product, location, qty, reserved in Quant._read_group(
                domain, ["product_id", "location_id"], ["quantity:sum", "reserved_quantity:sum"]):
            if qty - reserved > 0:
                stock[product.id]["places"].append((location, qty - reserved))
        qc_roots = self.env["stock.location"].sudo().search([("delivery_monitor_role", "=", "qc")])
        if not qc_roots:
            return stock
        qc_quants = [(product, lot, qty - reserved) for product, location, lot, qty, reserved in Quant._read_group(
            domain + [("lot_id", "!=", False), ("location_id", "child_of", qc_roots.ids)],
            ["product_id", "location_id", "lot_id"], ["quantity:sum", "reserved_quantity:sum"])
            if qty - reserved > 0 and role(location) == "qc"]
        inspected = self._inspected_lots(self.env["stock.lot"].union(*[lot for _p, lot, _q in qc_quants]))
        for product, lot, qty in qc_quants:
            stock[product.id]["qc_in" if lot.id in inspected else "qc_out"] += qty
        return stock

    @api.model
    def _line_stock(self, stock, src, need, role, sub):
        """Issue of one product line of a Waiting document: free MST stock under the source location (without
        QC, sample, excluded and agent locations) first, then QC. The agent stock is information only."""
        mst, qc, agent = defaultdict(float), 0.0, 0.0
        for location, qty in stock["places"]:
            place_role = role(location)
            if place_role == "agent":
                agent += qty
            elif location.parent_path.startswith(src.parent_path):
                if place_role == "qc":
                    qc += qty
                elif not place_role:
                    mst[sub(src, location)] += qty
        free = sum(mst.values())
        if need <= free:
            issue = "available"
        elif need <= free + qc:
            issue = "qc"
        elif free + qc > 0:
            issue = "short"
        else:
            issue = "none"
        return {"issue": issue, "mst": dict(mst), "qc": qc, "qc_in": stock["qc_in"], "qc_out": stock["qc_out"],
                "agent": agent}

    # ------------------------------------------------------- for the screen
    @api.model
    def get_monitor_data(self, date_str=False, period="today", scope="operational", category="all"):
        """Summary for the Monitor Pengiriman screen."""
        day, rows = self._get_rows(date_str, period, scope, category)
        main = [r for r in rows if not r["late_done"]]

        def block(items, keys, label_of, row_key):
            result = {key: {"key": key, "label": label_of[key], "doc": 0, "yds": 0.0, "kg": 0.0, "roll": 0} for key in keys}
            for row in items:
                entry = result[row[row_key]]
                entry["doc"] += 1
                entry["yds"] += row["yds"]
                entry["kg"] += row["kg"]
                entry["roll"] += row["roll"]
            return list(result.values())

        statuses = block(main, dict(STATUSES), dict(STATUSES), "status")
        waiting = [r for r in main if r["status"] == "waiting"]
        issues = block(waiting, dict(ISSUES), dict(ISSUES), "issue")
        qc_rows = [r for r in waiting if r["issue"] == "qc"]
        issues[1]["note"] = "Sudah inspect %s · belum inspect %s" % (
            _num(sum(r["qc_in"] for r in qc_rows)), _num(sum(r["qc_out"] for r in qc_rows)))
        categories = block(main, dict(CATEGORIES), dict(CATEGORIES), "category")
        for entry in categories:
            done = sum(r["yds"] for r in main if r["category"] == entry["key"] and r["status"] == "done")
            entry["done_pct"] = round(done / entry["yds"] * 100) if entry["yds"] else 0
        places = defaultdict(lambda: {"doc": 0, "roll": 0})
        for row in main:
            for place, rolls in row["subs"].items():
                places[place]["doc"] += 1
                places[place]["roll"] += rolls
        late_done = [r for r in rows if r["late_done"]]
        receipt = self.env["mst.receipt.monitor.line"]
        return {
            "date": fields.Date.to_string(day),
            "updated": datetime.now(receipt._get_tz()).strftime("%H:%M"),
            "count": len(main),
            "statuses": statuses,
            "total": {k: sum(s[k] for s in statuses) for k in ("doc", "yds", "kg", "roll")},
            "issues": issues,
            "kinds": [k for k in block(main, dict(KINDS), dict(KINDS), "kind") if k["doc"]],
            "categories": categories,
            "places": sorted(({"key": k, "label": k, **v} for k, v in places.items()), key=lambda p: -p["roll"]),
            "late_done": block(late_done, ["done"], {"done": "Done"}, "status")[0],
            "ages": [a for a in block(main, dict(AGES), dict(AGES), "age") if a["key"] != "today"],
        }

    @api.model
    def action_open_list(self, date_str, period, scope, category, panel=False, key=False):
        """Documents of one row of the summary in a list view (group by and Export)."""
        day, rows = self._get_rows(date_str, period, scope, category)
        keep = {
            "status": lambda r: r["status"] == key,
            "issue": lambda r: r["status"] == "waiting" and r["issue"] == key,
            "kind": lambda r: r["kind"] == key,
            "category": lambda r: r["category"] == key,
            "place": lambda r: key in r["subs"],
            "age": lambda r: r["age"] == key,
        }.get(panel, lambda r: True)
        rows = [r for r in rows if r["late_done"] == (panel == "late_done") and keep(r)]
        lines = self.create([{
            "monitor_date": day,
            "period": period,
            "picking_id": r["picking"].id,
            "partner_id": r["picking"].partner_id.id,
            "origin": r["picking"].origin,
            "kind": r["kind"],
            "category": r["category"],
            "scheduled_date": r["picking"].scheduled_date,
            "date_done": r["picking"].date_done,
            "status": r["status"],
            "issue": r["issue"],
            "late_done": r["late_done"],
            "age_days": r["age_days"],
            "age": r["age"],
            "yds": r["yds"],
            "kg": r["kg"],
            "roll": r["roll"],
            "free_stock": r["free_stock"],
            "note": r["note"],
            "product_line_ids": [(0, 0, p) for p in r["products"]],
        } for r in rows])
        labels = {"status": dict(STATUSES), "issue": dict(ISSUES), "kind": dict(KINDS), "category": dict(CATEGORIES),
                  "age": dict(AGES), "late_done": {False: _("Tertunda, selesai hari ini")}}
        label = labels.get(panel, {}).get(key, key) if panel else dict(PERIODS)[period]
        group_by = "issue" if panel == "issue" or (panel == "status" and key == "waiting") else False
        return {
            "type": "ir.actions.act_window",
            "name": "%s · %s" % (label, day.strftime("%d/%m/%Y")),
            "res_model": self._name,
            "view_mode": "tree,form",
            "views": [(self.env.ref("mst17_delivery_monitor.delivery_monitor_line_view_tree").id, "list"),
                      (self.env.ref("mst17_delivery_monitor.delivery_monitor_line_view_form").id, "form")],
            "search_view_id": self.env.ref("mst17_delivery_monitor.delivery_monitor_line_view_search").id,
            "domain": [("id", "in", lines.ids)],
            "context": {"search_default_group_%s" % group_by: 1} if group_by else {},
        }

    def action_open_picking(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "res_model": "stock.picking",
            "views": [(False, "form")],
            "res_id": self.picking_id.id,
        }


class DeliveryMonitorProduct(models.TransientModel):
    _name = "mst.delivery.monitor.product"
    _description = "Monitor Pengiriman Product Line"

    line_id = fields.Many2one("mst.delivery.monitor.line", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Produk", readonly=True)
    category = fields.Selection(CATEGORIES, string="Kategori", readonly=True)
    qty_text = fields.Char(string="Diminta", readonly=True)
    issue = fields.Selection(ISSUES, string="Kendala baris", readonly=True)
    free_stock = fields.Char(string="Stok bebas per sub gudang", readonly=True)
    qc_stock = fields.Char(string="QC0500", readonly=True)
    agent_stock = fields.Char(string="MTX (info)", readonly=True)
