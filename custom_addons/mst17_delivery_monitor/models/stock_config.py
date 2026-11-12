from odoo import fields, models


class StockPickingType(models.Model):
    _inherit = "stock.picking.type"

    delivery_monitor_kind = fields.Selection(
        [("customer", "Customer (WH/OUT)"), ("subcon", "Subcon (WH/RES)"), ("agent", "Agen MTX (WH/MTX)")],
        string="Monitor Pengiriman",
        help="Dokumen jenis ini masuk Monitor Pengiriman. Customer dan Subcon hanya yang berbintang "
             "(picking ticket dirilis PPIC); Agen MTX semuanya.")


class ProductCategory(models.Model):
    _inherit = "product.category"

    delivery_monitor_group = fields.Selection(
        [("denim", "Denim & BBSF"), ("greige", "Greige")], string="Kelompok Monitor Pengiriman",
        help="Kosong = ikut kategori induk. Tanpa isian sampai kategori teratas = Non-Denim.")


class StockLocation(models.Model):
    _inherit = "stock.location"

    delivery_monitor_role = fields.Selection(
        [("qc", "QC (mis. QC0500)"), ("sample", "Sample (mis. SAM0500)"),
         ("excluded", "Tidak dihitung (mis. TTP)"), ("agent", "Stok agen (info, mis. MTX/Stock)")],
        string="Peran Monitor Pengiriman",
        help="Sub-lokasi ikut lokasi induknya. Lokasi tanpa peran di bawah lokasi asal dokumen = stok bebas MST.")
