from odoo import fields, models


class CrmTeam(models.Model):
    _inherit = "crm.team"

    sample_location_id = fields.Many2one(
        "stock.location", string="Lokasi Pemakaian Sample",
        help="Destination of the sample requests of this team (e.g. SL -> Pemakaian Sample Agen SL). "
             "Empty: the company default.")


class ResCompany(models.Model):
    _inherit = "res.company"

    sample_location_id = fields.Many2one(
        "stock.location", string="Lokasi Pemakaian Sample (default)",
        help="Destination of sample requests for teams without their own location and users without a team.")
    sample_picking_type_id = fields.Many2one(
        "stock.picking.type", string="Jenis Transfer Sampel", domain=[("code", "=", "internal")],
        help="Operation type of the Internal Transfer made for a free sample (Gudang MST: Internal Transfers). "
             "Needed before a free sample can be processed.")


class StockLocation(models.Model):
    _inherit = "stock.location"

    sample_source = fields.Boolean(
        string="Asal Potong Sampel",
        help="Proposed as source location of a sample (the one with the most free stock), e.g. L0500, SAM0500.")
