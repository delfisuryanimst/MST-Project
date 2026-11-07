from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    sequence = env["ir.sequence"]
    for conversion in env["qc.conversion"].search([("reference", "in", [False, "New"])], order="id"):
        conversion.reference = sequence.next_by_code("qc.conversion")
