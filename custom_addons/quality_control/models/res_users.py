from odoo import models


class ResUsers(models.Model):
    _inherit = "res.users"

    def _get_qc_team(self):
        """First QC Team the user belongs to."""
        return self.env["qc.team"].search([("user_ids", "in", self.ids)], limit=1)
