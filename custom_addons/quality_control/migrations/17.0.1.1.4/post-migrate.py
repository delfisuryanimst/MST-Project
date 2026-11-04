from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    env["qc.dashboard"]._create_team_cards(env["qc.team"].search([]))

    for alert in env["qc.alert"].search([("team_id", "=", False), ("check_id.team_id", "!=", False)]):
        alert.team_id = alert.check_id.team_id
