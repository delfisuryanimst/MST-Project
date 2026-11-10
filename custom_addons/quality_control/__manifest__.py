{
    "name": "Quality Control",
    "version": "17.0.1.1.7",
    "summary": "Quality Checks, QC Points, Quality Alerts, QC Teams",
    "description": """
Quality Control (Community)
===========================
Kerangka modul Quality Control berdasarkan User Guidebook "Quality Control v17":

* QC Teams, QC Points, Alert Stages, Alert Tags (Configuration)
* Quality Checks (Pass/Fail, Measurement, Take a Picture, Text)
* Quality Alerts
* Dashboard per kelompok operasi (Pending / Failed / Partially Passed / Passed QC)
* All Operations: daftar transfer berdasarkan status QC
* Inspeksi kain 4 Point System (lebar inch, detail cacat, nilai grading)
* Quality Alert: partner, fail qty, proposed downgrade/scrap/return/transfer, Scrap langsung
* Request QC (On Demand) dari transfer
* Reporting (Pivot, Graph, List) & cetak PDF Quality Check
""",
    "category": "Inventory/Quality",
    "author": "Delfi",
    "license": "LGPL-3",
    "depends": ["stock", "mail", "web"],
    "data": [
        "security/qc_security.xml",
        "security/ir.model.access.csv",
        "data/qc_sequence_data.xml",
        "data/qc_location_data.xml",
        "data/qc_alert_stage_data.xml",
        "data/qc_dashboard_data.xml",
        "views/qc_team_views.xml",
        "views/qc_point_views.xml",
        "views/qc_alert_stage_views.xml",
        "views/qc_alert_tag_views.xml",
        "views/qc_check_views.xml",
        "wizard/qc_alert_execute_wizard_views.xml",
        "views/qc_alert_views.xml",
        "views/qc_conversion_views.xml",
        "views/qc_bulk_validate_actions.xml",
        "views/stock_picking_views.xml",
        "views/stock_move_line_views.xml",
        "views/qc_dashboard_views.xml",
        "wizard/qc_alert_wizard_views.xml",
        "wizard/qc_report_wizard_views.xml",
        "views/qc_menus.xml",
        "report/qc_check_report.xml",
        "report/qc_summary_reports.xml",
    ],
    "application": True,
    "installable": True,
}
