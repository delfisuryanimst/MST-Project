def migrate(cr, version):
    """The 'To Do' quality state key was renamed from 'none' to 'todo'."""
    cr.execute("UPDATE qc_check SET quality_state = 'todo' WHERE quality_state = 'none'")
