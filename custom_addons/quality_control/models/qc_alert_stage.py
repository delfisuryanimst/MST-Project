from odoo import fields, models


class QcAlertStage(models.Model):
    _name = "qc.alert.stage"
    _description = "Quality Alert Stage"
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True, translate=True)
    sequence = fields.Integer(default=10)
    fold = fields.Boolean(string="Folded in Kanban")
    done = fields.Boolean(string="Alert Processed", help="Alert pada stage ini dianggap selesai.")
    approver_ids = fields.Many2many("res.users", string="Approved by")
