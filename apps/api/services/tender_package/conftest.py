"""Unit regressions must not depend on a locally installed office application."""
import pytest


@pytest.fixture(autouse=True)
def isolate_external_office_renderer(monkeypatch):
    from .goods_render import LibreOfficeRenderer
    monkeypatch.setattr(LibreOfficeRenderer,'available',lambda self:False)
