from types import SimpleNamespace

import pytest

from api.services.knowledge.access import (
    PERM_MANAGE,
    PERM_USE,
    PERM_VIEW,
    perms_for_bases,
)
from api.services.menus import (
    ALL_MENUS,
    apply_tender_library_menu,
    resolve_menus,
    user_is_admin,
)


class _EmptyResult:
    def scalars(self):
        return self

    def all(self):
        return []


class _FakeDb:
    async def execute(self, _stmt):
        return _EmptyResult()


@pytest.mark.asyncio
async def test_tender_lib_not_auto_usable() -> None:
    user = SimpleNamespace(id=2, is_superuser=False, roles=[])
    base = SimpleNamespace(id=11, public_id="tenderlib01", created_by=1)
    mmap = await perms_for_bases(_FakeDb(), user, [base])
    assert mmap[11] == set()


@pytest.mark.asyncio
async def test_tender_lib_creator_keeps_manage() -> None:
    user = SimpleNamespace(id=7, is_superuser=False, roles=[])
    base = SimpleNamespace(id=11, public_id="tenderlib01", created_by=7)
    mmap = await perms_for_bases(_FakeDb(), user, [base])
    assert mmap[11] == {PERM_VIEW, PERM_USE, PERM_MANAGE}


@pytest.mark.asyncio
async def test_admin_gets_all_perms() -> None:
    user = SimpleNamespace(id=1, is_superuser=True, roles=[])
    base = SimpleNamespace(id=11, public_id="tenderlib01", created_by=9)
    mmap = await perms_for_bases(_FakeDb(), user, [base])
    assert mmap[11] == {PERM_VIEW, PERM_USE, PERM_MANAGE}


def test_apply_tender_library_menu_strips_without_manage() -> None:
    menus = ["/", "/ai-chat", "/tenders", "/tender-library", "/quotes"]
    out = apply_tender_library_menu(menus, can_manage=False)
    assert "/tender-library" not in out
    assert "/tenders" in out


def test_apply_tender_library_menu_inserts_in_nav_order() -> None:
    menus = ["/", "/tenders", "/quotes"]
    out = apply_tender_library_menu(menus, can_manage=True)
    assert "/tender-library" in out
    assert out.index("/tenders") < out.index("/tender-library")
    assert [h for h in ALL_MENUS if h in set(out)] == out


def test_operator_role_menus_omit_tender_library() -> None:
    user = SimpleNamespace(
        is_superuser=False,
        roles=[SimpleNamespace(code="operator")],
    )
    assert not user_is_admin(user)
    assert "/tender-library" not in resolve_menus(user)
    assert "/tenders" in resolve_menus(user)
    assert "/logs" not in resolve_menus(user)
