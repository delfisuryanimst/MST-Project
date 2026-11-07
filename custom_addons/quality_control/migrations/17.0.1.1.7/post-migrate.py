from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Old SMO stock moves were named after the free title (Downgrade); name them after the SMO number."""
    env = api.Environment(cr, SUPERUSER_ID, {})
    for move in env["stock.move"].search([("qc_conversion_id", "!=", False)]):
        reference = move.qc_conversion_id.reference
        if reference and move.name != reference:
            move.name = reference
