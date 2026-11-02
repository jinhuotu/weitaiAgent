"""Request-local library metadata consumed by synchronous slot collection."""


class SlotCatalog(list):
    def __init__(self, items=(), *, dismissed_keys=(), real_keys=()):
        super().__init__(items)
        self.dismissed_keys = frozenset(dismissed_keys)
        self.real_keys = frozenset(real_keys)
