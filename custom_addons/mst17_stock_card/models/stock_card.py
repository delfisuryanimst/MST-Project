from datetime import datetime, time, timedelta

import pytz

from odoo import _, api, fields, models
from odoo.exceptions import UserError

SIGN = {"in": 1, "out": -1, "trf": 0}
WHERE_SQL = """
     WHERE sml.state = 'done' AND sml.product_id = %(product)s
       AND (sml.location_id = ANY(%(scope)s) OR sml.location_dest_id = ANY(%(scope)s))
"""


class StockCard(models.TransientModel):
    _name = "mst.stock.card"
    _description = "Kartu Stok"

    product_id = fields.Many2one("product.product", "Product", required=True, domain=[("type", "=", "product")])
    warehouse_id = fields.Many2one("stock.warehouse", "Warehouse")
    location_id = fields.Many2one("stock.location", "Location", domain="[('id', 'in', allowed_location_ids)]")
    rack_id = fields.Many2one(
        "stock.location", "Rak", domain="[('location_id', '=', location_id), ('usage', '=', 'internal')]")
    allowed_location_ids = fields.Many2many("stock.location", compute="_compute_allowed_location_ids")
    date_from = fields.Date(
        "Tanggal Mulai", required=True, default=lambda self: fields.Date.context_today(self).replace(day=1))
    date_to = fields.Date("Tanggal Akhir", required=True, default=fields.Date.context_today)
    opening = fields.Float("Saldo Awal", digits="Product Unit of Measure", readonly=True)
    line_ids = fields.One2many("mst.stock.card.line", "card_id")

    @api.depends("warehouse_id")
    def _compute_allowed_location_ids(self):
        # Location = level 3 (WH/Stock/xxxx): internal children of a warehouse Stock location.
        for card in self:
            warehouses = card.warehouse_id or self.env["stock.warehouse"].search([])
            card.allowed_location_ids = self.env["stock.location"].search(
                [("location_id", "in", warehouses.lot_stock_id.ids), ("usage", "=", "internal")])

    @api.onchange("warehouse_id", "location_id")
    def _onchange_scope(self):
        if self.location_id not in self.allowed_location_ids._origin:
            self.location_id = False
        if self.rack_id.location_id != self.location_id:
            self.rack_id = False

    def _scope(self):
        domain = [("usage", "=", "internal")]
        root = self.rack_id or self.location_id or self.warehouse_id.view_location_id
        if root:
            domain.append(("id", "child_of", root.id))
        return self.env["stock.location"].with_context(active_test=False).search(domain).ids

    def _utc(self, day):
        tz = pytz.timezone(self.env.user.tz or "Asia/Jakarta")
        return tz.localize(datetime.combine(day, time.min)).astimezone(pytz.utc).replace(tzinfo=None)

    def action_show(self):
        self.ensure_one()
        if self.date_from > self.date_to:
            raise UserError(_("Tanggal Mulai tidak boleh setelah Tanggal Akhir."))
        scope = self._scope()
        start, end = self._utc(self.date_from), self._utc(self.date_to + timedelta(days=1))
        params = {"product": self.product_id.id, "scope": scope, "start": start, "end": end}
        self.env.flush_all()
        cr = self.env.cr
        cr.execute("""
            SELECT sml.location_id, sml.location_dest_id, SUM(sml.quantity_product_uom)
              FROM stock_move_line sml""" + WHERE_SQL + """
               AND sml.date < %(start)s
          GROUP BY 1, 2
        """, params)
        before = cr.fetchall()
        # One row per Reference + From + To; partner from the picking, else from the SO / PO.
        cr.execute("""
            SELECT sml.reference, sml.location_id, sml.location_dest_id, MIN(sml.date), SUM(sml.quantity_product_uom),
                   (ARRAY_AGG(COALESCE(sp.partner_id, so.partner_id, po.partner_id) ORDER BY sml.id))[1]
              FROM stock_move_line sml
         LEFT JOIN stock_move sm ON sm.id = sml.move_id
         LEFT JOIN stock_picking sp ON sp.id = sml.picking_id
         LEFT JOIN sale_order_line sol ON sol.id = sm.sale_line_id
         LEFT JOIN sale_order so ON so.id = sol.order_id
         LEFT JOIN purchase_order_line pol ON pol.id = sm.purchase_line_id
         LEFT JOIN purchase_order po ON po.id = pol.order_id""" + WHERE_SQL + """
               AND sml.date >= %(start)s AND sml.date < %(end)s
          GROUP BY 1, 2, 3
          ORDER BY 4, MIN(sml.id)
        """, params)
        rows = cr.fetchall()

        # Every row touches the scope, so the From/To side tells IN / OUT / TRF.
        scope = set(scope)

        def kind(src, dst):
            return ("trf" if dst in scope else "out") if src in scope else "in"

        opening = sum(SIGN[kind(src, dst)] * qty for src, dst, qty in before)
        locations = self.env["stock.location"].browse({loc for row in rows for loc in row[1:3]})
        partner_side = set(locations.filtered(lambda loc: loc.usage in ("customer", "supplier")).ids)
        product, uom = self.product_id.id, self.product_id.uom_id.id
        balance = opening
        vals = [{"card_id": self.id, "sequence": 0, "date": start, "reference": _("Saldo Awal"),
                 "product_id": product, "uom_id": uom, "balance": opening}]
        for seq, (ref, src, dst, date, qty, partner) in enumerate(rows, 1):
            move_type = kind(src, dst)
            balance += SIGN[move_type] * qty
            vals.append({
                "card_id": self.id, "sequence": seq, "date": date, "reference": ref, "product_id": product,
                "uom_id": uom, "location_id": src, "location_dest_id": dst, "quantity": qty, "move_type": move_type,
                "partner_id": partner if partner_side & {src, dst} else False, "balance": balance,
            })
        self.line_ids.unlink()
        self.env["mst.stock.card.line"].create(vals)
        self.opening = opening
        return {
            "type": "ir.actions.act_window",
            "name": _("Kartu Stok %s", self.product_id.display_name),
            "res_model": "mst.stock.card.line",
            "view_mode": "tree",
            "domain": [("card_id", "=", self.id)],
        }


class StockCardLine(models.TransientModel):
    _name = "mst.stock.card.line"
    _description = "Kartu Stok Line"
    _order = "card_id, sequence"

    card_id = fields.Many2one("mst.stock.card", required=True, ondelete="cascade", index=True)
    sequence = fields.Integer()
    date = fields.Datetime("Date")
    reference = fields.Char("Reference")
    product_id = fields.Many2one("product.product", "Product")
    uom_id = fields.Many2one("uom.uom", "UoM")
    location_id = fields.Many2one("stock.location", "From")
    location_dest_id = fields.Many2one("stock.location", "To")
    partner_id = fields.Many2one("res.partner", "Partner")
    quantity = fields.Float("Qty", digits="Product Unit of Measure")
    move_type = fields.Selection([("in", "IN"), ("out", "OUT"), ("trf", "TRF")], "IN/OUT")
    balance = fields.Float("Balance", digits="Product Unit of Measure")
