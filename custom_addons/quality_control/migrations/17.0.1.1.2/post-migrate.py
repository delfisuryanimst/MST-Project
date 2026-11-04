from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    env = api.Environment(cr, SUPERUSER_ID, {})
    checks = env["qc.check"].search([])

    for check in checks:
        partner = check.picking_id._get_qc_partner() if check.picking_id else env["res.partner"]
        if check.partner_id != partner:
            check.partner_id = partner

    cr.execute("""
        UPDATE qc_check SET yard_asal = qty_to_test
         WHERE COALESCE(yard_asal, 0) = 0 AND COALESCE(qty_to_test, 0) <> 0
    """)
    cr.execute("UPDATE qc_check SET control_date = create_date WHERE control_date IS NULL")

    for check in checks.filtered(lambda c: not c.team_id):
        team = check.point_id.team_id or env["qc.team"]._get_team_for_user(check.user_id or check.create_uid)
        if team:
            check.team_id = team

    for alert in env["qc.alert"].search([("partner_id", "=", False), ("check_id", "!=", False)]):
        alert.partner_id = alert.check_id.partner_id
