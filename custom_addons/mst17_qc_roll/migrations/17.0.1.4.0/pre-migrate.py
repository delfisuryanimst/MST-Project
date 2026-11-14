def migrate(cr, version):
    """"B Grade Product" became "Result Product" (also used for a product change A to A): keep the data."""
    cr.execute("""
        SELECT 1 FROM information_schema.columns
         WHERE table_name = 'mst_qc_inspection' AND column_name = 'product_down_id'
    """)
    if cr.fetchone():
        cr.execute("ALTER TABLE mst_qc_inspection RENAME COLUMN product_down_id TO product_result_id")
        cr.execute("""
            UPDATE ir_model_fields SET name = 'product_result_id'
             WHERE model = 'mst.qc.inspection' AND name = 'product_down_id'
        """)
