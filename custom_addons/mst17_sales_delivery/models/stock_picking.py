import re

from odoo import api, fields, models

DOC_NAME = re.compile(r"WH/(?:OUT|IN)/\d+")


class StockPicking(models.Model):
    _inherit = "stock.picking"

    admin_return = fields.Boolean(
        "Retur admin", copy=False,
        help="Koreksi dokumen, bukan barang kembali. Tidak dihitung di Data Pengiriman, "
             "kecuali sebagai kiriman asli / pengganti dalam satu rantai retur admin.")
    replacement_of_id = fields.Many2one(
        "stock.picking", "Pengganti dari", copy=False, index="btree_not_null",
        domain="[('picking_type_code', '=', 'outgoing'), ('id', '!=', id)]",
        help="Kiriman asli (WH/OUT) yang diretur admin dan diganti oleh dokumen ini.")
    delivery_excluded = fields.Boolean(
        "Tidak dihitung di Data Pengiriman", copy=False, readonly=True, index=True,
        help="Diisi otomatis dari Retur admin dan Pengganti dari.")

    def write(self, vals):
        res = super().write(vals)
        if {"admin_return", "replacement_of_id"} & set(vals):
            self._update_delivery_exclusion()
        return res

    def _action_done(self):
        res = super()._action_done()
        if self.filtered(lambda p: p.admin_return or p.replacement_of_id or p.return_id.admin_return):
            self._update_delivery_exclusion()
        return res

    @api.model
    def _update_delivery_exclusion(self):
        """Count each admin-return chain once (FDD R-06); other admin-marked transfers are not counted."""
        Picking = self.sudo()
        engine = self.env["sales.delivery.engine"]
        returns = engine._admin_returns()
        excluded = Picking.search([("admin_return", "=", True)]) | returns
        counted = Picking.browse()
        for chain in engine._chains(returns):
            counted |= chain["counted"]
            excluded |= chain["original"] | chain["replacements"]
        excluded -= counted
        (Picking.search([("delivery_excluded", "=", True)]) - excluded).write({"delivery_excluded": False})
        excluded.filtered(lambda p: not p.delivery_excluded).write({"delivery_excluded": True})

    @api.model
    def _init_admin_return_from_notes(self):
        """One-time fill from the old note texts ("RETUR ADMIN", "WH/OUT BARU", "Pengganti ex WH/OUT/x")."""
        self.env.cr.execute("""
            UPDATE stock_picking SET admin_return = TRUE
             WHERE note::text ILIKE '%%retur adm%%' OR note::text ILIKE '%%WH/OUT BARU%%'
        """)
        self.invalidate_model(["admin_return"])
        returns = self.env["sales.delivery.engine"]._admin_returns()
        originals = {picking.name: picking.return_id for picking in returns}
        originals.update({original.name: original for original in returns.return_id})
        candidates = self.sudo().search([
            ("picking_type_code", "=", "outgoing"), ("state", "=", "done"),
            ("replacement_of_id", "=", False), ("note", "ilike", "WH/"),
        ])
        for picking in candidates:
            for name in DOC_NAME.findall(picking.note):
                original = originals.get(name)
                if original and original != picking:
                    super(StockPicking, picking).write({"replacement_of_id": original.id})
                    break
        self._update_delivery_exclusion()
