import json
import math
import uuid
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError

PARAM = "mst17_order_calculator."
SCENARIOS = ("p25", "p50", "p90")
MONTHS = ("Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des")
STOCK_SCENARIOS = {
    "p25": "Barang datang tepat waktu",
    "p50": "Terlambat biasa (perkiraan)",
    "p90": "Terlambat seperti kasus terlambat",
}
FRESH_SCENARIOS = {"p25": "Paling cepat", "p50": "Perkiraan", "p90": "Paling lambat"}
DEVELOPMENTS = [("LD", "Labdip"), ("SO", "Strike off"), ("HL", "Handloom")]
OPEN_MOVE_STATES = ("waiting", "confirmed", "partially_available", "assigned")
LATE_SO_DAYS = 14
MIN_HISTORY_SAMPLES = 20
DISCLAIMER = "Perhitungan ini adalah estimasi dari data Odoo dan histori pengiriman, bukan janji kirim."
GREIGE_TIPS = {
    "yes": "Greige cukup, tidak perlu menunggu greige. Booking greige dulu sebelum quotation.",
    "no": "Greige belum cukup, ditambah waktu makloon greige. Booking greige dulu sebelum quotation.",
}


def _fmt(number):
    """1234.5 -> '1.234,5' (Indonesian format, no trailing zero)."""
    text = "{:,.1f}".format(number)
    if text.endswith(".0"):
        text = text[:-2]
    return text.translate({ord(","): ".", ord("."): ","})


def _round(number):
    return math.floor(number + 0.5)


def _percentiles(values):
    values = sorted(values)
    last = len(values) - 1

    def pick(q):
        k = last * q
        low = int(k)
        high = min(low + 1, last)
        return round(values[low] + (values[high] - values[low]) * (k - low), 1)

    return {"n": len(values), "p25": pick(0.25), "p50": pick(0.5), "p90": pick(0.9)}


class OrderCalculatorEngine(models.AbstractModel):
    _name = "order.calculator.engine"
    _description = "Order Calculator Engine"

    @api.model
    def _history(self):
        return json.loads(self.env["ir.config_parameter"].sudo().get_param(PARAM + "history", "{}"))

    @api.model
    def _process_selection(self):
        return [(key, "(semua proses)" if key == "_all" else key) for key in self._history().get("process", {})]

    # ------------------------------------------------------------------ data

    @api.model
    def _warehouses(self):
        """The user's Available Warehouses, admins included. Unlike the live rule, blank means none here.
        Without that field (module not installed) every warehouse of the company is used."""
        user = self.env.user
        if "allowed_warehouse_ids" in user._fields:
            warehouses = user.allowed_warehouse_ids.sudo()
        else:
            warehouses = self.sudo().env["stock.warehouse"].search([])
        warehouses = warehouses.filtered(lambda w: w.company_id == self.env.company)
        if not warehouses:
            raise UserError(_("Tidak ada gudang yang bisa dihitung untuk user ini. Isi Available Warehouses di user."))
        return warehouses

    @api.model
    def _product_data(self, product):
        """Stock, incoming and outgoing of one product in the warehouses allowed for the user.
        Read with sudo: sales users may not read POs or other sales' orders, only this summary leaves."""
        env = self.sudo().env
        today = fields.Date.context_today(self)
        warehouses = self._warehouses()
        product = product.sudo().with_context(warehouse=warehouses.ids)
        quant_domain, in_domain, out_domain = product._get_domain_locations()

        # Stock location = rack, input / quality control location = QC (standard warehouse fields);
        # sub-locations with a role override it. The nearest one decides, other locations are not counted.
        roles = [(loc, loc.order_calc_role) for loc in env["stock.location"].search(
            [("warehouse_id", "in", warehouses.ids), ("order_calc_role", "!=", False)])]
        for warehouse in warehouses:
            roles += [(warehouse.lot_stock_id, "rack"), (warehouse.wh_input_stock_loc_id, "qc"),
                      (warehouse.wh_qc_stock_loc_id, "qc")]
        roles = sorted((r for r in roles if r[0]), key=lambda r: -len(r[0].parent_path))
        held, qc_names = {"rack": 0.0, "qc": 0.0, "excluded": 0.0}, set()
        for location, quantity in env["stock.quant"]._read_group(
                [("product_id", "=", product.id)] + quant_domain, ["location_id"], ["quantity:sum"]):
            match = next(((loc, r) for loc, r in roles if location.parent_path.startswith(loc.parent_path)), None)
            if match:
                held[match[1]] += quantity
                if match[1] == "qc" and quantity:
                    qc_names.add(match[0].name)

        country = self.env.company.country_id
        Move = env["stock.move"]
        open_domain = [("product_id", "=", product.id), ("state", "in", OPEN_MOVE_STATES)]

        supply = []
        for move in Move.search(open_domain + in_domain, order="date, id"):
            order = move.purchase_line_id.order_id
            vendor_country = order.partner_id.commercial_partner_id.country_id
            date = fields.Date.context_today(self, move.date)
            supply.append({
                "qty": move.product_qty,
                "date": date,
                "day": (date - today).days,
                "kind": "PO" if order else (move.picking_type_id.name or "Masuk"),
                "import": bool(order and vendor_country and vendor_country != country),
            })

        demand = []
        for move in Move.search(open_domain + out_domain, order="date, id"):
            order = move.sale_line_id.order_id
            date = fields.Date.context_today(self, order.commitment_date or move.date)
            demand.append({
                "qty": move.product_qty,
                "date": date,
                "day": (date - today).days,
                "sales": order.user_id.name or "",
                "kind": "SO" if order else (move.picking_type_id.name or "Keluar"),
            })

        is_import = bool(country) and bool(env["purchase.order.line"].search_count([
            ("product_id", "=", product.id),
            ("state", "in", ("purchase", "done")),
            ("order_id.partner_id.country_id", "not in", [False, country.id]),
        ], limit=1))

        return {
            "product": product,
            "warehouses": warehouses,
            "today": today,
            "uom": product.uom_id.name,
            "fresh": product.categ_id.order_calc_fresh,
            "import": is_import,
            "qc": held["qc"],
            "qc_name": ", ".join(sorted(qc_names)),
            "rack": max(0.0, held["rack"]),
            "supply": supply,
            "demand": demand,
        }

    @api.model
    def _summary(self, product):
        data = self._product_data(product)
        hist = self._history()
        incoming = sum(
            x["qty"] for x in data["supply"]
            if x["day"] >= -hist["delay_po_import" if x["import"] else "delay_po_local"]["p90"])
        return {
            "rack": data["rack"],
            "qc": data["qc"],
            "demand": sum(x["qty"] for x in data["demand"]),
            "incoming": incoming,
            "fresh": data["fresh"],
        }

    @api.model
    def _alternatives(self, data):
        """Other colours of the same article (same name before the first ';') that still have free stock."""
        product = data["product"]
        article = product.name.split(";")[0].strip()
        others = self.env["product.product"].sudo().with_context(warehouse=data["warehouses"].ids).search([
            ("name", "=like", article + ";%"),
            ("categ_id", "=", product.categ_id.id),
            ("id", "!=", product.id),
        ], limit=50)
        result = []
        for other, row in zip(others, others.read(["qty_available", "outgoing_qty"])):
            free = row["qty_available"] - row["outgoing_qty"]
            if free > 0:
                parts = other.name.split(";")
                result.append({"name": parts[4].strip() if len(parts) > 4 else other.display_name, "free": free})
        return sorted(result, key=lambda r: -r["free"])[:3]

    # ----------------------------------------------------------- calculation

    def _stock_plan(self, data, hist, qty, scenario):
        qc_days = hist["qc_days"][scenario]
        events, stale = [{"day": 0, "qty": data["rack"], "label": "Siap di rak sekarang", "out": False}], []
        if data["qc"] > 0:
            events.append({"day": qc_days, "qty": data["qc"],
                           "label": "Selesai QC (sekarang di %s)" % data["qc_name"], "out": False})
        for x in data["supply"]:
            delay = hist["delay_po_import" if x["import"] else "delay_po_local"]
            if x["day"] < -delay["p90"]:
                stale.append(x)
                continue
            day = x["day"] + (max(0, delay["p25"]) if scenario == "p25" else delay[scenario])
            events.append({"day": max(day, 1) + qc_days, "qty": x["qty"], "out": False,
                           "label": "%s (rencana %s)" % (x["kind"], self._date(data, x["day"]))})
        for x in data["demand"]:
            events.append({"day": max(0, x["day"]), "qty": -x["qty"], "out": True,
                           "label": "%s · %s" % (x["kind"], x["sales"]) if x["sales"] else x["kind"]})
        # FIFO: on the same day, outgoing goes before incoming.
        events.sort(key=lambda e: (e["day"], not e["out"]))

        balance = 0.0
        for event in events:
            balance += event["qty"]
            event["balance"] = balance
        # floor[i] = lowest balance from event i onwards: what can still be taken without hurting later orders.
        floor, low = [0.0] * len(events), math.inf
        for i in range(len(events) - 1, -1, -1):
            low = min(low, events[i]["balance"])
            floor[i] = low

        now = max(i for i, e in enumerate(events) if e["day"] <= 0)
        ready_day = next((
            e["day"] for i, e in enumerate(events)
            if (i == len(events) - 1 or events[i + 1]["day"] != e["day"]) and floor[i] >= qty
        ), None)
        return {"events": events, "floor": floor, "stale": stale,
                "free_now": max(0.0, floor[now]), "ready_day": ready_day}

    @staticmethod
    def _available_at(plan, day):
        index = [i for i, e in enumerate(plan["events"]) if e["day"] <= day]
        return max(0.0, plan["floor"][index[-1]]) if index else 0.0

    def _fresh_parts(self, data, hist, opts, scenario):
        parts = []
        if opts.get("development"):
            low, high = hist["development"][opts["development"]]
            label = dict(DEVELOPMENTS)[opts["development"]]
            parts.append(("Development " + label, {"p25": low, "p50": _round((low + high) / 2), "p90": high}[scenario]))
        if data["import"]:
            parts.append(("PO impor → diterima", _round(hist["lt_import"][scenario])))
        else:
            if opts.get("greige") != "yes":
                parts.append(("Greige (makloon)", _round(hist["greige"][scenario])))
            process = opts.get("process") or "_all"
            lead = hist["process"].get(process, hist["process"]["_all"])
            parts.append(("Produksi " + ("semua proses" if process == "_all" else process), _round(lead[scenario])))
        parts += [("QC", hist["qc_days"][scenario]), ("Siapkan & kirim", hist["ship_days"][scenario])]
        return parts

    # --------------------------------------------------------------- display

    def _date(self, data, day):
        date = data["today"] + timedelta(days=math.floor(day))
        text = "%s %s" % (date.day, MONTHS[date.month - 1])
        return text if date.year == data["today"].year else "%s %s" % (text, date.year)

    @staticmethod
    def _date_full(date):
        return "%s %s %s" % (date.day, MONTHS[date.month - 1], date.year)

    def _qty(self, data, qty):
        return "%s %s" % (_fmt(qty), data["uom"])

    @api.model
    def _result_values(self, product, qty, need_date=False, opts=None):
        """Result as plain data (JSON-safe): used by the HTML template and the OWL widget."""
        data = self._product_data(product)
        hist = self._history()
        need_day = (need_date - data["today"]).days if need_date else None
        values = (self._fresh_values if data["fresh"] else self._stock_values)(data, hist, qty, need_day, opts or {})
        values.update(self._stamp(hist), disclaimer=DISCLAIMER, uom=data["uom"],
                      warehouse=", ".join(data["warehouses"].mapped("name")))
        return values

    @api.model
    def _stamp(self, hist=None):
        """When the stock was read (now) and when the history was last refreshed."""
        updated = (hist or self._history()).get("updated")
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return {
            "calculated": "%s, %s" % (self._date_full(now.date()), now.strftime("%H:%M")),
            "updated": self._date_full(fields.Date.to_date(updated)) if updated else "-",
        }

    @api.model
    def _render(self, product, qty, need_date=False, opts=None):
        values = self._result_values(product, qty, need_date, opts)
        # Unique name for the radio buttons of the CSS tabs (form and dialog can be open at once).
        values["uid"] = "mst_calc_%s" % uuid.uuid4().hex[:8]
        return self.env["ir.qweb"]._render("mst17_order_calculator.result", values)

    def _stock_values(self, data, hist, qty, need_day, opts):
        plans = {s: self._stock_plan(data, hist, qty, s) for s in SCENARIOS}
        plan = plans["p50"]
        q = lambda x: self._qty(data, x)  # noqa: E731
        ready = lambda p: "±" + self._date(data, p["ready_day"]) if p["ready_day"] is not None else "—"  # noqa: E731

        if need_day is None:
            available = plan["free_now"]
            if available >= qty:
                alert = ("ok", "Bisa dikirim sekarang", "%s tersedia tanpa mengganggu order lain." % q(qty))
            elif plan["ready_day"] is not None:
                alert = ("warn", "Sebagian sekarang, lengkap %s" % ready(plan) if available else "Siap %s" % ready(plan),
                         "Sekarang bebas %s; kekurangan %s dari barang yang akan datang." % (q(available), q(qty - available)))
            else:
                alert = ("bad", "Belum cukup dari stok dan barang yang akan datang",
                         "Maksimal %s. Sisanya perlu produksi / pembelian baru." % q(available))
            columns, numeric = ["Kalau barang yang akan datang…", "Lengkap"], []
            rows = [{"highlight": s == "p50", "cells": [
                STOCK_SCENARIOS[s], "Sekarang" if plans[s]["free_now"] >= qty else ready(plans[s])]} for s in SCENARIOS]
        else:
            check_day = need_day - hist["ship_days"]["p50"]
            avail = {s: self._available_at(plans[s], check_day) for s in SCENARIOS}
            available, need = avail["p50"], self._date(data, need_day)
            from_rack = min(qty, plan["free_now"])
            source = ("Dari stok di rak %s, sisanya %s dari barang selesai QC / datang / WIP sebelum tanggal itu."
                      % (q(from_rack), q(qty - from_rack)) if qty > from_rack else "Stok di rak sudah cukup sekarang.")
            if avail["p90"] >= qty:
                alert = ("ok", "Cukup untuk dikirim %s" % need, source + " Tetap cukup walaupun barang datang terlambat.")
            elif avail["p50"] >= qty:
                alert = ("ok", "Cukup untuk dikirim %s" % need,
                         source + " Kalau barang terlambat seperti kasus terlambat, baru lengkap %s." % ready(plans["p90"]))
            elif avail["p25"] >= qty:
                alert = ("warn", "Cukup untuk %s hanya bila barang datang tepat waktu" % need,
                         "Pada perkiraan biasa baru tersedia %s; lengkap %s." % (q(available), ready(plan)))
            else:
                alert = ("bad", "Belum cukup untuk %s" % need,
                         "Pada tanggal itu bisa disiapkan ±%s, kurang %s. %s" % (
                             q(available), q(qty - available),
                             "Lengkap %s (perkiraan)." % ready(plan) if plan["ready_day"] is not None
                             else "Sisanya perlu produksi / pembelian baru."))
            columns, numeric = ["Kalau barang yang akan datang…", "Siap %s" % need, "Status", "Lengkap"], [1]
            rows = []
            for s in SCENARIOS:
                enough = avail[s] >= qty
                rows.append({"highlight": s == "p50", "status": "ok" if enough else "bad", "cells": [
                    STOCK_SCENARIOS[s], q(min(qty, avail[s])),
                    "✓ Cukup" if enough else "Kurang %s" % _fmt(qty - avail[s]),
                    "" if enough else ready(plans[s])]})

        tips = []
        if 0 < available < qty:
            tips.append("Tawarkan kirim bertahap: %s %s, sisa %s %s." % (
                q(available), "sekarang" if need_day is None else "pada " + self._date(data, need_day), q(qty - available),
                ready(plan) if plan["ready_day"] is not None else "setelah produksi baru"))
        late = [x for x in data["demand"] if x["kind"] == "SO" and x["day"] < -LATE_SO_DAYS]
        if late:
            tips.append("%s SO lain sudah lewat tanggal tapi belum terkirim (%s). Tanyakan PPIC apakah masih berlaku."
                        % (len(late), q(sum(x["qty"] for x in late))))
        if plan["free_now"] < qty:
            alternatives = self._alternatives(data)
            if alternatives:
                tips.append("Alternatif warna lain artikel yang sama: %s." % ", ".join(
                    "%s (%s)" % (a["name"], q(a["free"])) for a in alternatives))
        if plan["free_now"] >= qty or plan["ready_day"] is not None:
            tips.append("Kalau customer setuju, buat quotation dan Book Stock supaya qty terkunci.")
        warning = False
        if plan["stale"]:
            warning = "Ada %s sisa PO lama (%s) yang tanggalnya sudah lewat jauh; tidak dihitung. Konfirmasi ke MD." % (
                len(plan["stale"]), q(sum(x["qty"] for x in plan["stale"])))

        return {
            "alerts": [alert],
            "columns": columns,
            "numeric": numeric,
            "rows": rows,
            "tips": tips,
            "warning": warning,
            "orders": [{
                "sales": x["sales"], "kind": x["kind"], "qty": _fmt(x["qty"]),
                "date": self._date(data, x["day"]) + (" (lewat)" if x["day"] < 0 else ""),
                "qty_value": x["qty"], "day": x["day"], "past": x["day"] < 0,
            } for x in data["demand"]],
            "sequence": [{
                "date": "Hari ini" if e["day"] <= 0 else "±" + self._date(data, e["day"]),
                "label": e["label"], "qty": ("+" if e["qty"] > 0 else "") + _fmt(e["qty"]), "balance": _fmt(e["balance"]),
            } for e in plan["events"]] + [{
                "date": "Hari ini" if plan["ready_day"] is not None and plan["ready_day"] <= 0 else ready(plan),
                "label": "Order Anda", "qty": "−" + _fmt(qty),
                "balance": "≥ 0" if plan["ready_day"] is not None else "", "highlight": True,
            }],
        }

    def _fresh_values(self, data, hist, qty, need_day, opts):
        start = (opts["approve_date"] - data["today"]).days if opts.get("approve_date") else 0
        parts = {s: self._fresh_parts(data, hist, opts, s) for s in SCENARIOS}
        end = {s: start + sum(p[1] for p in parts[s]) for s in SCENARIOS}
        alerts = [("info", "Fresh order%s: siap kirim ±%s" % (" impor" if data["import"] else "", self._date(data, end["p50"])),
                   "Paling cepat %s, paling lambat %s, dihitung dari order di-ACC %s." % (
                       self._date(data, end["p25"]), self._date(data, end["p90"]), self._date(data, start)))]
        if need_day is not None:
            met = [s for s in SCENARIOS if end[s] <= need_day]
            need = self._date(data, need_day)
            if len(met) == 3:
                alerts.append(("ok", "Tanggal %s realistis" % need, "Terpenuhi bahkan pada kasus terlambat."))
            elif "p50" in met:
                alerts.append(("warn", "Tanggal %s mungkin" % need, "Terpenuhi pada perkiraan biasa."))
            elif "p25" in met:
                alerts.append(("warn", "Tanggal %s berisiko" % need, "Hanya bila semua tahap lancar."))
            else:
                alerts.append(("bad", "Tanggal %s sulit" % need,
                               "Usulkan ±%s atau kirim bertahap." % self._date(data, end["p50"])))

        if data["import"]:
            lead, source = hist["lt_import"], "PO impor sampai diterima"
        else:
            process = opts.get("process") or "_all"
            lead = hist["process"].get(process, hist["process"]["_all"])
            source = "produksi " + ("semua proses" if process == "_all" else process)
        tips = ["Histori %s: %s–%s hari (n %s)." % (source, _round(lead["p25"]), _round(lead["p90"]), lead["n"])]
        if not data["import"]:
            # "Sesuai data" needs greige stock per article, which is not linked yet: same as the mockup without data.
            tips.append(GREIGE_TIPS.get(opts.get("greige"), "Data greige artikel tidak ditemukan; pilih manual."))
        return {
            "alerts": alerts,
            "columns": ["Skenario", "Tahapan (hari)", "Total", "Siap kirim"],
            "numeric": [2],
            "rows": [{"highlight": s == "p50", "cells": [
                FRESH_SCENARIOS[s], " + ".join("%s %s" % p for p in parts[s]),
                "%s hr" % (end[s] - start), self._date(data, end[s])]} for s in SCENARIOS],
            "tips": tips,
            "warning": False,
            "orders": False,
            "sequence": False,
        }

    # ------------------------------------------------------------------ cron

    @api.model
    def _cron_update_history(self):
        """Refresh the PO delay and import lead time from the receipts of the last year.
        Production, QC and shipping days stay as set in the system parameter."""
        ICP = self.env["ir.config_parameter"].sudo()
        hist = json.loads(ICP.get_param(PARAM + "history", "{}"))
        country = self.env.company.country_id.id or 0
        #keterlambatan PO = tanggal penerimaan - tanggal rencana PO; 
        #lead time impor = tanggal penerimaan - tanggal approve PO.
        self.env.cr.execute("""
            SELECT EXTRACT(EPOCH FROM sm.date - pol.date_planned) / 86400,
                   EXTRACT(EPOCH FROM sm.date - po.date_approve) / 86400,
                   COALESCE(rp.country_id, %s) != %s
              FROM stock_move sm
              JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
              JOIN purchase_order po ON po.id = pol.order_id
              JOIN res_partner rp ON rp.id = po.partner_id
             WHERE sm.state = 'done' AND sm.date >= now() - interval '1 year'
        """, (country, country))
        samples = {"delay_po_local": [], "delay_po_import": [], "lt_import": []}
        for delay, lead, is_import in self.env.cr.fetchall():
            samples["delay_po_import" if is_import else "delay_po_local"].append(float(delay))
            if is_import and lead is not None:
                samples["lt_import"].append(float(lead))
        for key, values in samples.items():
            if len(values) >= MIN_HISTORY_SAMPLES:
                hist[key] = _percentiles(values)
        hist["updated"] = fields.Date.to_string(fields.Date.context_today(self))
        ICP.set_param(PARAM + "history", json.dumps(hist, indent=1))
