from types import SimpleNamespace

from api.services.menus import (
    ADMIN_ONLY_MENUS,
    ALL_MENUS,
    sanitize_role_menus,
    resolve_menus,
    user_is_admin,
)


def test_auditor_has_no_logs() -> None:
    user = SimpleNamespace(
        is_superuser=False,
        roles=[SimpleNamespace(code="auditor")],
    )
    menus = resolve_menus(user)
    assert "/logs" not in menus
    assert "/logs" in ADMIN_ONLY_MENUS
    assert "/approval" in menus
    assert "/tender-tasks" in menus


def test_operator_keeps_business_menus() -> None:
    user = SimpleNamespace(
        is_superuser=False,
        roles=[SimpleNamespace(code="operator")],
    )
    menus = resolve_menus(user)
    assert "/tenders" in menus
    assert "/quotes" in menus
    assert "/logs" not in menus
    assert "/users" not in menus


def test_custom_role_without_menus_only_home() -> None:
    user = SimpleNamespace(
        is_superuser=False,
        roles=[SimpleNamespace(code="role_abc")],
    )
    assert resolve_menus(user) == ["/"]


def test_empty_roles_only_home() -> None:
    user = SimpleNamespace(is_superuser=False, roles=[])
    assert resolve_menus(user) == ["/"]


def test_role_menus_union_strips_admin_only() -> None:
    user = SimpleNamespace(
        is_superuser=False,
        roles=[
            SimpleNamespace(code="role_a", menus=["/", "/tenders"]),
            SimpleNamespace(code="role_b", menus=["/", "/quotes", "/logs", "/users"]),
        ],
    )
    menus = resolve_menus(user)
    assert "/tenders" in menus
    assert "/quotes" in menus
    assert "/logs" not in menus
    assert "/users" not in menus


def test_admin_gets_logs() -> None:
    user = SimpleNamespace(is_superuser=True, roles=[])
    assert user_is_admin(user)
    menus = resolve_menus(user)
    assert menus == list(ALL_MENUS)
    assert "/logs" in menus


def test_sanitize_role_menus_drops_logs() -> None:
    out = sanitize_role_menus(["/tenders", "/logs", "/nope", "/tenders", None])
    assert out == ["/", "/tenders"]
