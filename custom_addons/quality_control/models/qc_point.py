from odoo import _, api, fields, models
from odoo.exceptions import ValidationError

TEST_TYPES = [
    ("pass_fail", "Pass - Fail"),
    ("measure", "Measurement"),
    ("picture", "Take a Picture"),
    ("text", "Text"),
]


class QcPoint(models.Model):
    _name = "qc.point"
    _description = "Quality Control Point"
    _inherit = ["mail.thread"]
    _order = "sequence, id"

    name = fields.Char(string="Title", required=True, tracking=True)
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company)

    product_category_ids = fields.Many2many("product.category")
    product_ids = fields.Many2many("product.product")
    picking_type_ids = fields.Many2many("stock.picking.type", required=True)

    team_id = fields.Many2one("qc.team", tracking=True)
    user_id = fields.Many2one("res.users", string="Responsible User", tracking=True)
    is_mandatory = fields.Boolean(
        string="QC Mandatory",
        help="If checked, the transfer cannot be validated until the Quality Check is done.",
    )
    measure_frequency = fields.Selection(
        [("all", "All"), ("on_demand", "On Demand")],
        string="Control Frequency", default="all", required=True, tracking=True,
    )
    partial_test = fields.Float(
        string="Partial Test (%)", default=100.0,
        help="Percentage of the product quantity that gets a QC.",
    )
    partial_test_by = fields.Selection([("product", "Per Product"), ("lot", "Per Lot")], default="product")
    test_type = fields.Selection(TEST_TYPES, string="Type", default="pass_fail", required=True)
    max_tests = fields.Integer(string="Maximum Number of Tests Allowed", default=1)

    norm = fields.Float()
    norm_unit = fields.Char(string="Unit of Measure", default="mm")
    tolerance_min = fields.Float()
    tolerance_max = fields.Float()

    note = fields.Html(string="Instructions")
    check_ids = fields.One2many("qc.check", "point_id", string="Quality Checks")

    @api.constrains("partial_test")
    def _check_partial_test(self):
        for point in self:
            if not 0 < point.partial_test <= 100:
                raise ValidationError(_("Partial Test must be between 0 and 100%."))

    @api.constrains("tolerance_min", "tolerance_max", "test_type")
    def _check_tolerance(self):
        for point in self:
            if point.test_type == "measure" and point.tolerance_min > point.tolerance_max:
                raise ValidationError(_("Tolerance Min cannot be greater than Tolerance Max."))

    @api.model
    def _get_points(self, picking_type, frequency, company=None):
        if not picking_type:
            return self.browse()
        domain = [
            ("picking_type_ids", "in", picking_type.id),
            ("measure_frequency", "=", frequency),
        ]
        if company:
            domain.append(("company_id", "in", [False, company.id]))
        return self.sudo().search(domain)

    def _prepare_check_vals(self):
        """Values every new QC Check takes from its QC Point."""
        self.ensure_one()
        return {
            "point_id": self.id,
            "team_id": self._get_team().id,
            "user_id": self.user_id.id or self.env.user.id,
            "test_type": self.test_type,
        }

    def _get_team(self):
        self.ensure_one()
        return self.team_id or self.env["qc.team"]._get_team_for_user(self.env.user)

    def _applies_to_product(self, product):
        self.ensure_one()
        if self.product_ids:
            return product in self.product_ids
        if self.product_category_ids:
            return bool(self.env["product.category"].search_count([
                ("id", "child_of", self.product_category_ids.ids),
                ("id", "=", product.categ_id.id),
            ]))
        return True
