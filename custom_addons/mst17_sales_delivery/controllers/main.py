from odoo import http
from odoo.http import content_disposition, request


class SalesDeliveryController(http.Controller):

    @http.route("/mst17_sales_delivery/export/<int:report_id>", type="http", auth="user")
    def export(self, report_id):
        report = request.env["sales.delivery.report"].browse(report_id).exists()
        report.check_access_rule("read")
        content, filename = report._export_xlsx()
        return request.make_response(content, headers=[
            ("Content-Type", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("Content-Disposition", content_disposition(filename)),
        ])
