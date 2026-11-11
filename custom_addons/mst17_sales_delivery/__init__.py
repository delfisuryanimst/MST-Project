from . import controllers, models


def post_init_hook(env):
    env["stock.picking"]._init_admin_return_from_notes()
