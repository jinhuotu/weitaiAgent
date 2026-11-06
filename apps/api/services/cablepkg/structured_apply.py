from .adapters import confirm_offer, confirm_price

def apply_offers(session, candidates, *, actor, basis):
    result = session
    for c in candidates:
        result = confirm_offer(result, c["solidified_id"], c["target"], str(c["value"]), actor=actor, basis=basis, status=c.get("status"))
    return result

def apply_prices(session, candidates, *, actor, basis):
    result = session
    for c in candidates:
        result = confirm_price(result, package_key=c["package_key"], document_id=c["document_id"], table=int(c["table"]), row=int(c["row"]), column=int(c["column"]), amount=c["amount"], actor=actor, basis=basis, currency=c.get("currency", "CNY"), unit=c["unit"])
    return result
