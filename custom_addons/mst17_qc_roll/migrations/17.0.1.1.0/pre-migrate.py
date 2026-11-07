def migrate(cr, version):
    """A split piece that keeps the roll ID is inspected again on the same QCP, so the rule
    "one inspection per roll per QCP" becomes "one draft inspection per roll" (checked in Python)."""
    cr.execute("ALTER TABLE mst_qc_inspection DROP CONSTRAINT IF EXISTS mst_qc_inspection_picking_lot_unique")
    cr.execute("DELETE FROM ir_model_constraint WHERE name = 'mst_qc_inspection_picking_lot_unique'")
