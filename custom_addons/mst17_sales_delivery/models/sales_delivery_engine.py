import io
import re
from collections import defaultdict
from datetime import datetime, time, timedelta

import xlsxwriter
from dateutil.relativedelta import relativedelta

from odoo import api, fields, models

WIB = timedelta(hours=7)  # FDD: day and month boundaries follow WIB (UTC+7)
MONTHS = ["Jan", "Feb", "Mar", "Apr", "Mei", "Jun", "Jul", "Agu", "Sep", "Okt", "Nov", "Des"]
MONTHS_FULL = ["Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September",
               "Oktober", "November", "Desember"]
OZ_TO_GSM = 33.906
KANTONG = "kain kantong"
SAME_TOTAL = 0.005  # PPIC decision 20 Oct 2026: under 0.5% difference = revision without qty change
CHAIN_KINDS = {"same": ("revisi non-qty", "gr"), "qty": ("revisi qty", "bad"), "none": ("tanpa pengganti", "gr")}
KIND_LABELS = {"yds": "yds", "yarn_kg": "Benang (kg)", "yarn_unit": "Benang", "kg": "Kg tanpa GSM / lebar",
               "other": "Lainnya"}
PAYMENT = {"paid": ("Lunas", "ok"), "in_payment": ("Lunas", "ok"), "partial": ("Sebagian", "gr"),
           "reversed": ("Dibalik", "gr")}


def _numbers(text):
    return [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", text or "")]


def _gsm(text):
    numbers = _numbers(text)
    if not numbers:
        return 0.0
    return numbers[0] * OZ_TO_GSM if "oz" in text.lower() else numbers[0]


def _width(text):
    """Width finish in inch; a range like 44/45" uses the smallest number."""
    numbers = _numbers(text)
    return 0.0 if not numbers or "oz" in text.lower() else min(numbers)


def _fmt(value, digits=1):
    text = "{:,.{}f}".format(value or 0.0, digits).replace(",", "_").replace(".", ",").replace("_", ".")
    return text[:-2] if digits and text.endswith(",0") else text


def _wib_date(value):
    return (value + WIB).date() if value else False


def _date(day, year=True):
    text = "%s %s" % (day.day, MONTHS[day.month - 1])
    return "%s %s" % (text, day.year) if year else text


class SalesDeliveryEngine(models.AbstractModel):
    _name = "sales.delivery.engine"
    _description = "Data Pengiriman Engine"

    # ------------------------------------------------------------------ units

    def _converter(self, product_ids):
        """Function (product_id, uom, qty) -> (kind, value, netto factor). Only kind "yds" counts in yds."""
        yard, kg = self.env.ref("uom.product_uom_yard"), self.env.ref("uom.product_uom_kgm")
        products = self.env["product.product"].sudo().browse(product_ids)
        sku = "sku_gsm_finish_id" in products._fields  # SKU fields come from mst_stock on live
        units = {
            product.id: (
                product.categ_id.delivery_yarn,
                _gsm(product.sku_gsm_finish_id.name) if sku else 0.0,
                _width(product.sku_width_finish_id.name) if sku else 0.0,
                0.5 if sku and (product.sku_pemakaian_id.name or "").strip().lower() == KANTONG else 1.0,
            ) for product in products
        }

        def convert(product_id, uom, qty):
            yarn, gsm, width, factor = units[product_id]
            weight = uom.category_id == kg.category_id
            kilos = weight and uom._compute_quantity(qty, kg, round=False)
            if yarn:
                return ("yarn_kg", kilos, factor) if weight else ("yarn_unit", qty, factor)
            if uom.category_id == yard.category_id:
                return "yds", uom._compute_quantity(qty, yard, round=False), factor
            if weight:
                if gsm and width:
                    return "yds", kilos * 1000 / (gsm * width * 0.0254) / 0.9144, factor
                return "kg", kilos, factor
            return "other", qty, factor

        return convert

    def _picking_totals(self, pickings):
        """Total per picking, in yds where possible (used to compare an original with its replacement)."""
        moves = pickings.move_ids.filtered(lambda m: m.state == "done")
        convert = self._converter(moves.product_id.ids)
        totals = defaultdict(float)
        for move in moves:
            totals[move.picking_id.id] += convert(move.product_id.id, move.product_uom, move.quantity)[1]
        return totals

    # ------------------------------------------------------------ admin return

    @api.model
    def _admin_returns(self):
        return self.env["stock.picking"].sudo().search([
            ("state", "=", "done"), ("picking_type_code", "=", "incoming"), ("return_id", "!=", False),
            "|", ("admin_return", "=", True), ("return_id.admin_return", "=", True),
        ])

    @api.model
    def _chains(self, returns):
        """Original WH/OUT -> admin return WH/IN -> replacement WH/OUT, counted once."""
        Picking = self.env["stock.picking"].sudo()
        originals = returns.return_id
        replacements = Picking.search([
            ("replacement_of_id", "in", (returns | originals).ids),
            ("state", "=", "done"), ("picking_type_code", "=", "outgoing"),
        ])
        back, new = defaultdict(Picking.browse), defaultdict(Picking.browse)
        for picking in returns:
            back[picking.return_id.id] |= picking
        for picking in replacements:
            origin = picking.replacement_of_id
            new[(origin.return_id or origin).id] |= picking
        totals = self._picking_totals(originals | replacements)
        chains = []
        for original in originals:
            replaced = new[original.id]
            if not replaced:
                kind, counted = "none", Picking.browse()
            else:
                before, after = totals[original.id], sum(totals[p.id] for p in replaced)
                same = abs(before - after) < SAME_TOTAL * before if before else not after
                kind, counted = ("same", original) if same else ("qty", replaced)
            chains.append({"original": original, "returns": back[original.id], "replacements": replaced,
                           "kind": kind, "counted": counted, "totals": totals})
        return chains

    # ------------------------------------------------------------------- data

    def _scope(self):
        """FDD R-11: Own Documents = self, All Documents = own teams, Administrator / PPIC = all."""
        user = self.env.user
        if user.has_group("mst17_sales_delivery.group_delivery_all"):
            return {"all": True, "team_ids": [], "user_id": user.id}
        teams = self.env["crm.team"].sudo().search(["|", ("member_ids", "in", user.ids), ("user_id", "=", user.id)])
        team_ids = teams.ids if user.has_group("sales_team.group_sale_salesman_all_leads") else []
        return {"all": False, "team_ids": team_ids, "user_id": user.id}

    def _lines(self, start, end, scope, team_id=False, user_id=False):
        """Done SO moves to (delivery) or from (return) a customer location, between two WIB dates."""
        query = """
            SELECT sm.id, sm.product_id, sm.product_uom, sm.quantity, sm.sale_line_id,
                   (sm.date + interval '7 hours')::date AS day, sl.usage = 'customer' AS is_return,
                   sp.id AS picking_id, sp.name AS picking, rp.name AS origin,
                   so.partner_id, sol.salesman_id, so.team_id
              FROM stock_move sm
              JOIN sale_order_line sol ON sol.id = sm.sale_line_id
              JOIN sale_order so ON so.id = sol.order_id
              JOIN stock_location sl ON sl.id = sm.location_id
              JOIN stock_location dl ON dl.id = sm.location_dest_id
              LEFT JOIN stock_picking sp ON sp.id = sm.picking_id
              LEFT JOIN stock_picking rp ON rp.id = sp.return_id
             WHERE sm.state = 'done' AND sm.company_id = ANY(%s)
               AND sm.date >= %s AND sm.date < %s
               AND (sl.usage = 'customer') <> (dl.usage = 'customer')
               AND NOT COALESCE(sp.delivery_excluded, FALSE)
        """
        params = [self.env.companies.ids, datetime.combine(start, time.min) - WIB,
                  datetime.combine(end + timedelta(days=1), time.min) - WIB]
        if not scope["all"]:
            query += " AND (sol.salesman_id = %s OR so.team_id = ANY(%s))"
            params += [scope["user_id"], scope["team_ids"]]
        if team_id:
            query += " AND so.team_id = %s"
            params.append(team_id)
        if user_id:
            query += " AND sol.salesman_id = %s"
            params.append(user_id)
        self.env.cr.execute(query, params)
        rows = self.env.cr.dictfetchall()
        if rows:
            convert = self._converter({r["product_id"] for r in rows})
            uoms = {u.id: u for u in self.env["uom.uom"].browse({r["product_uom"] for r in rows})}
            for row in rows:
                row["kind"], row["value"], row["factor"] = convert(row["product_id"], uoms[row["product_uom"]],
                                                                    row["quantity"])
        return rows

    @staticmethod
    def _sum(lines, netto):
        total = defaultdict(float)
        sent_docs, return_docs = set(), set()
        for line in lines:
            value, factor = line["value"], line["factor"]
            if line["is_return"]:
                return_docs.add(line["picking_id"])
            else:
                sent_docs.add(line["picking_id"])
            if line["kind"] != "yds":
                if not line["is_return"]:
                    total[line["kind"]] += value
            elif line["is_return"]:
                total["returned"] += value * factor if netto else value
                total["netto"] -= value * factor
            else:
                total["sent"] += value
                total["netto"] += value * factor
                if factor < 1:
                    total["kantong"] += value
                    total["cut"] += value * (1 - factor)
        total["main"] = total["netto"] if netto else total["sent"]
        return total, sent_docs, return_docs

    # ----------------------------------------------------------------- report

    def _periods(self, period, month, until):
        today = _wib_date(fields.Datetime.now())
        if period == "month":
            start = month
            end = start + relativedelta(months=1, days=-1)
            compare = (start - relativedelta(months=1), start - timedelta(days=1))
            return {"start": start, "end": end, "first": start - relativedelta(months=5), "compare": compare,
                    "label": "%s %s" % (MONTHS_FULL[start.month - 1], start.year),
                    "vs": "vs %s %s" % (MONTHS[compare[0].month - 1], compare[0].year)}
        end = today if period == "ytd_today" else until
        start = end.replace(month=1, day=1)
        return {"start": start, "end": end, "first": start,
                "compare": (start - relativedelta(years=1), end - relativedelta(years=1)),
                "label": "Jan – %s" % _date(end), "vs": "vs periode sama %s" % (start.year - 1)}

    @api.model
    def _report(self, period, month, until, team_id=False, user_id=False, measure="bruto", details=False):
        self.env.flush_all()
        netto = measure == "netto"
        p = self._periods(period, month, until)
        scope = self._scope()
        chart_lines = self._lines(p["first"], p["end"], scope, team_id, user_id)
        lines = [line for line in chart_lines if line["day"] >= p["start"]]
        compare_start, compare_end = p["compare"]
        if compare_start >= p["first"]:
            previous = [line for line in chart_lines if compare_start <= line["day"] <= compare_end]
        else:
            previous = self._lines(compare_start, compare_end, scope, team_id, user_id)
        total, sent_docs, return_docs = self._sum(lines, netto)
        before = self._sum(previous, netto)[0]["main"]

        names = self._names(lines)
        invoices = self._invoices(lines)
        values = {
            "measure": measure,
            "label": p["label"],
            "cards": self._cards(total, before, p, sent_docs, return_docs, netto, invoices),
            "chart": self._chart(chart_lines, p),
            "chart_title": "6 bulan terakhir" if period == "month" else "Januari – %s %s" % (
                MONTHS_FULL[p["end"].month - 1], p["end"].year),
            "tabs": self._tabs(lines, total, names, netto),
            "chains": self._chain_table(p, scope, team_id, user_id),
            "stamp": self._stamp(),
        }
        values["tabs"].append(self._invoice_tab(invoices))
        if details:
            values["details"] = self._details(lines, names, netto)
        return values

    def _names(self, lines):
        def read(model, ids):
            return {r.id: r.display_name for r in self.env[model].sudo().browse(i for i in ids if i)}
        return {
            "partner": read("res.partner", {line["partner_id"] for line in lines}),
            "user": read("res.users", {line["salesman_id"] for line in lines}),
            "team": read("crm.team", {line["team_id"] for line in lines}),
            "product": read("product.product", {line["product_id"] for line in lines}),
        }

    def _stamp(self):
        now = fields.Datetime.context_timestamp(self, fields.Datetime.now())
        return "%s, %s" % (_date(now.date()), now.strftime("%H:%M"))

    def _cards(self, total, before, p, sent_docs, return_docs, netto, invoices):
        change = False
        if before:
            pct = (total["main"] - before) / before * 100
            change = {"text": "%s %s%% %s" % ("▲" if pct >= 0 else "▼", _fmt(abs(pct)), p["vs"]),
                      "trend": "up" if pct >= 0 else "down"}
        if netto:
            return [
                {"label": "Netto", "value": "%s yds" % _fmt(total["netto"], 0), "change": change},
                {"label": "Dikirim (bruto)", "value": "%s yds" % _fmt(total["sent"], 0), "note": p["label"]},
                {"label": "Kain Kantong (50%)", "value": "− %s yds" % _fmt(total["cut"], 0),
                 "note": "dari %s yds kain kantong" % _fmt(total["kantong"], 0)},
                {"label": "Retur customer", "value": "− %s yds" % _fmt(total["returned"], 0),
                 "note": "%s dokumen retur" % len(return_docs)},
                {"label": "Invoice posted",
                 "value": self._money(sum(i.amount_total_signed for i in invoices)) if invoices is not None else "–",
                 "note": "sisa tagihan %s" % self._money(sum(i.amount_residual_signed for i in invoices))
                 if invoices is not None else "tidak ada akses invoice"},
            ]
        return [
            {"label": "Total dikirim", "value": "%s yds" % _fmt(total["sent"], 0), "change": change},
            {"label": "Retur customer", "value": "%s yds" % _fmt(total["returned"], 0),
             "note": "ditampilkan, tidak mengurangi Bruto"},
            {"label": "Dokumen kirim", "value": "%s WH/OUT" % len(sent_docs), "note": "tanpa retur admin"},
            {"label": "Benang", "value": "%s kg" % _fmt(total["yarn_kg"], 0),
             "note": "· %s bal (terpisah)" % _fmt(total["yarn_unit"], 0)},
            {"label": "Kg tanpa GSM / lebar", "value": "%s kg" % _fmt(total["kg"], 0),
             "note": "tidak dikonversi ke yds"},
        ]

    @staticmethod
    def _money(amount):
        if abs(amount) >= 1e9:
            return "Rp %s M" % _fmt(amount / 1e9, 2)
        return "Rp %s" % _fmt(amount, 0)

    @staticmethod
    def _chart(lines, p):
        months, cursor = [], p["first"]
        while cursor <= p["end"]:
            months.append(cursor)
            cursor += relativedelta(months=1)
        bars = {(m.year, m.month): [0.0, 0.0] for m in months}
        for line in lines:
            if line["kind"] == "yds":
                bars[(line["day"].year, line["day"].month)][1 if line["is_return"] else 0] += line["value"]
        return [{"label": MONTHS[m.month - 1], "sent": round(bars[(m.year, m.month)][0]),
                 "returned": round(bars[(m.year, m.month)][1]), "current": i == len(months) - 1}
                for i, m in enumerate(months)]

    def _tabs(self, lines, total, names, netto):
        def group(key, label_of, extra=()):
            groups = defaultdict(lambda: {"sent": 0.0, "returned": 0.0, "netto": 0.0, "docs": set()})
            for line in lines:
                g = groups[key(line)]
                if not line["is_return"]:
                    g["docs"].add(line["picking_id"])
                if line["kind"] != "yds":
                    continue
                value, factor = line["value"], line["factor"]
                if line["is_return"]:
                    g["returned"] += value * factor if netto else value
                    g["netto"] -= value * factor
                else:
                    g["sent"] += value
                    g["netto"] += value * factor
            rows = []
            for k, g in groups.items():
                main = g["netto"] if netto else g["sent"]
                rows.append([*label_of(k), g["sent"], g["returned"], *([g["netto"]] if netto else []),
                             *([len(g["docs"])] if extra else []), main / total["main"] * 100 if total["main"] else 0])
            return sorted(rows, key=lambda r: -r[-1])

        measures = [("Dikirim (yds)", "num"), ("Retur (yds)", "num")] + ([("Netto (yds)", "num")] if netto else [])
        sums = [total["sent"], total["returned"]] + ([total["netto"]] if netto else [])
        user = names["user"]

        def tab(key, label, heads, rows, docs=False):
            columns = heads + measures + ([("Dok. kirim", "int")] if docs else []) + [("% total", "pct")]
            foot = [""] * len(heads) + sums + ([sum(r[-2] for r in rows)] if docs else []) + [100 if rows else 0]
            foot[0] = "Total"
            return {"key": key, "label": label, "columns": [{"label": c, "type": t} for c, t in columns],
                    "rows": rows, "total": foot}

        salesperson = group(lambda l: (l["salesman_id"], l["team_id"]),
                            lambda k: (user.get(k[0], "(tanpa salesperson)"), names["team"].get(k[1], "-")), True)
        customer = group(lambda l: l["partner_id"], lambda k: (names["partner"].get(k, "-"),), True)
        product = group(lambda l: l["product_id"], lambda k: (names["product"].get(k, "-"),))

        sent, returned = {}, {}
        for line in lines:
            book = returned if line["is_return"] else sent
            doc = book.setdefault(line["picking_id"], {"line": line, "yds": 0.0, "netto": 0.0, "products": set()})
            doc["products"].add(names["product"].get(line["product_id"], "-"))
            if line["kind"] == "yds":
                value = line["value"] * (line["factor"] if netto and line["is_return"] else 1)
                doc["yds"] += value
                doc["netto"] += line["value"] * line["factor"]

        def who(line):
            return [line["day"].isoformat(), names["partner"].get(line["partner_id"], "-"),
                    user.get(line["salesman_id"], "-")]

        delivery_rows = sorted(([d["line"]["picking"] or "-", *who(d["line"]), d["yds"],
                                 *([d["netto"]] if netto else [])] for d in sent.values()), key=lambda r: r[1])
        return_rows = sorted(([d["line"]["picking"] or "-", d["line"]["origin"] or "-", *who(d["line"]),
                               ", ".join(sorted(d["products"])), d["yds"]] for d in returned.values()),
                             key=lambda r: r[2])
        when = [("Tanggal", "date"), ("Customer", "text"), ("Salesperson", "text")]
        return [
            tab("salesperson", "Per salesperson", [("Salesperson", "text"), ("Tim", "text")], salesperson, True),
            tab("customer", "Per customer", [("Customer", "text")], customer, True),
            tab("product", "Per produk", [("Produk", "text")], product),
            {"key": "delivery", "label": "Per pengiriman",
             "columns": [{"label": c, "type": t} for c, t in [("Dokumen", "text")] + when + [("Dikirim (yds)", "num")]
                         + ([("Netto (yds)", "num")] if netto else [])],
             "rows": delivery_rows,
             "total": ["Total", "", "", "", total["sent"]] + ([total["sent"] - total["cut"]] if netto else [])},
            {"key": "return", "label": "Retur",
             "columns": [{"label": c, "type": t} for c, t in [("Dokumen", "text"), ("Asal kirim", "text")] + when
                         + [("Produk", "text"), ("Retur (yds)", "num")]],
             "rows": return_rows, "total": ["Total", "", "", "", "", "", total["returned"]]},
        ]

    def _invoices(self, lines):
        """Posted customer invoices / credit notes of the delivered SO lines, with the user's own access."""
        Move = self.env["account.move"]
        if not Move.check_access_rights("read", raise_exception=False):
            return None
        sale_lines = list({line["sale_line_id"] for line in lines if not line["is_return"]})
        return Move.search([("move_type", "in", ("out_invoice", "out_refund")), ("state", "=", "posted"),
                            ("invoice_line_ids.sale_line_ids", "in", sale_lines)], order="invoice_date desc, id desc")

    def _invoice_tab(self, invoices):
        rows = []
        for invoice in invoices or []:
            status = ("Credit note", "ok") if invoice.move_type == "out_refund" else \
                PAYMENT.get(invoice.payment_state, ("Belum bayar", "gr"))
            rows.append([invoice.name, invoice.partner_id.display_name,
                         invoice.invoice_date and invoice.invoice_date.isoformat() or "",
                         invoice.invoice_date_due and invoice.invoice_date_due.isoformat() or "",
                         invoice.amount_total_signed, invoice.amount_residual_signed,
                         {"text": status[0], "pill": status[1]}])
        columns = [("Invoice", "text"), ("Customer", "text"), ("Tanggal", "date"), ("Jatuh tempo", "date"),
                   ("Total", "money"), ("Sisa", "money"), ("Status", "pill")]
        return {"key": "invoice", "label": "Invoice", "columns": [{"label": c, "type": t} for c, t in columns],
                "rows": rows, "total": ["Total", "", "", "", sum(r[4] for r in rows), sum(r[5] for r in rows), ""],
                "note": "Hak akses invoice Odoo tetap berlaku." if invoices is not None else
                "Anda tidak punya akses ke invoice."}

    def _chain_table(self, p, scope, team_id, user_id):
        rows = []
        for chain in self._chains(self._admin_returns()):
            original, replaced, totals = chain["original"], chain["replacements"], chain["totals"]
            order = original.sale_id
            days = [_wib_date(d) for d in (original | replaced).mapped("date_done")]
            if not any(p["start"] <= d <= p["end"] for d in days if d):
                continue
            if not scope["all"] and order.user_id.id != scope["user_id"] and order.team_id.id not in scope["team_ids"]:
                continue
            if (team_id and order.team_id.id != team_id) or (user_id and order.user_id.id != user_id):
                continue
            label, pill = CHAIN_KINDS[chain["kind"]]
            counted = {"same": "Asli, di %s" % MONTHS[_wib_date(original.date_done).month - 1],
                       "qty": "Pengganti, di bulannya", "none": "Tidak dihitung"}[chain["kind"]]
            rows.append([
                "%s · %s · %s yds" % (original.name, _date(_wib_date(original.date_done), False),
                                      _fmt(totals[original.id])),
                ", ".join("%s · %s · %s yds" % (r.name, _date(_wib_date(r.date_done), False), _fmt(totals[r.id]))
                          for r in replaced) or "–",
                {"text": label, "pill": pill}, counted,
            ])
        columns = [("Kiriman asli", "text"), ("Pengganti", "text"), ("Jenis", "pill"), ("Yang dihitung", "text")]
        return {"columns": [{"label": c, "type": t} for c, t in columns], "rows": rows}

    def _details(self, lines, names, netto):
        rows = []
        for line in sorted(lines, key=lambda l: (l["day"], l["picking"] or "")):
            yds = line["kind"] == "yds"
            uom = self.env["uom.uom"].browse(line["product_uom"])
            rows.append([line["day"].isoformat(), line["picking"] or "-", "Retur" if line["is_return"] else "Kirim",
                         names["partner"].get(line["partner_id"], "-"), names["user"].get(line["salesman_id"], "-"),
                         names["team"].get(line["team_id"], "-"), names["product"].get(line["product_id"], "-"),
                         line["quantity"] * (-1 if line["is_return"] else 1), uom.name, KIND_LABELS[line["kind"]],
                         line["value"] * (-1 if line["is_return"] else 1) if yds else "",
                         line["value"] * line["factor"] * (-1 if line["is_return"] else 1) if yds else ""])
        columns = [("Tanggal", "date"), ("Dokumen", "text"), ("Jenis", "text"), ("Customer", "text"),
                   ("Salesperson", "text"), ("Tim", "text"), ("Produk", "text"), ("Qty asli", "num"),
                   ("Satuan", "text"), ("Kelompok", "text"), ("yds", "num"), ("yds netto", "num")]
        return {"label": "Detail baris", "columns": [{"label": c, "type": t} for c, t in columns], "rows": rows}

    # ------------------------------------------------------------------ excel

    @api.model
    def _xlsx(self, data, filters):
        buffer = io.BytesIO()
        book = xlsxwriter.Workbook(buffer, {"in_memory": True})
        bold = book.add_format({"bold": True})
        head = book.add_format({"bold": True, "bg_color": "#F2F3F5", "border": 1})
        formats = {"num": book.add_format({"num_format": "#,##0.0"}), "int": book.add_format({"num_format": "0"}),
                   "pct": book.add_format({"num_format": "0.0"}), "money": book.add_format({"num_format": "#,##0"}),
                   "date": book.add_format({"num_format": "dd mmm yyyy"})}

        sheet = book.add_worksheet("Ringkasan")
        sheet.set_column(0, 0, 26)
        sheet.set_column(1, 2, 30)
        sheet.write(0, 0, "Data Pengiriman", bold)
        for row, (label, value) in enumerate(filters, start=1):
            sheet.write(row, 0, label)
            sheet.write(row, 1, value)
        row = len(filters) + 2
        for card in data["cards"]:
            sheet.write(row, 0, card["label"], bold)
            sheet.write(row, 1, card["value"])
            sheet.write(row, 2, (card.get("change") or {}).get("text") or card.get("note") or "")
            row += 1

        for table in data["tabs"] + [data["details"]]:
            sheet = book.add_worksheet(table["label"][:31])
            sheet.set_column(0, len(table["columns"]), 16)
            for col, column in enumerate(table["columns"]):
                sheet.write(0, col, column["label"], head)
            for r, cells in enumerate(table["rows"] + ([table["total"]] if table.get("total") else []), start=1):
                for col, cell in enumerate(cells):
                    kind = table["columns"][col]["type"]
                    if isinstance(cell, dict):
                        cell = cell["text"]
                    if kind == "date" and cell:
                        sheet.write_datetime(r, col, datetime.strptime(cell, "%Y-%m-%d"), formats["date"])
                    elif kind in formats and isinstance(cell, (int, float)):
                        sheet.write_number(r, col, cell, formats[kind])
                    else:
                        sheet.write(r, col, cell, bold if r == len(table["rows"]) + 1 else None)
        book.close()
        return buffer.getvalue()
