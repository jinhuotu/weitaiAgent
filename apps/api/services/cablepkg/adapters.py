"""Controlled exits from cablepkg to legacy package services."""
ENABLED = True

def confirm_offer(*args, **kwargs):
    if not ENABLED: raise RuntimeError("cablepkg adapters disabled")
    from api.services.tender_package.goods_write_gateway import confirm_offer as fn
    return fn(*args, **kwargs)

def confirm_price(*args, **kwargs):
    if not ENABLED: raise RuntimeError("cablepkg adapters disabled")
    from api.services.tender_package.goods_write_gateway import confirm_price as fn
    return fn(*args, **kwargs)
