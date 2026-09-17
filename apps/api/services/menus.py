"""菜单权限：按角色白名单返回可访问的导航 href。日志等管理页只给管理员。"""

from __future__ import annotations

from typing import Any, Iterable

from db.models.user import User

ADMIN_ONLY_MENUS = frozenset(
    {
        "/model-manage",
        "/prompt-manage",
        "/mcp-manage",
        "/scene-agents",
        "/workflows",
        "/users",
        "/logs",
    }
)

ALL_MENUS: tuple[str, ...] = (
    "/",
    "/ai-chat",
    "/work-tasks",
    "/tender-tasks",
    "/approval",
    "/knowledge",
    "/tenders",
    "/tender-qa",
    "/tender-library",
    "/quotes",
    "/prompt-manage",
    "/mcp-manage",
    "/scene-agents",
    "/model-manage",
    "/workflows",
    "/users",
    "/logs",
    "/settings",
)

BUSINESS_MENUS: tuple[str, ...] = tuple(h for h in ALL_MENUS if h not in ADMIN_ONLY_MENUS)

DEFAULT_NEW_ROLE_MENUS: tuple[str, ...] = ("/", "/ai-chat")

SEED_ROLE_MENUS: dict[str, tuple[str, ...]] = {
    "operator": ("/", "/ai-chat", "/work-tasks", "/tenders", "/tender-qa", "/quotes"),
    "auditor": ("/", "/ai-chat", "/tender-tasks", "/tender-qa", "/approval"),
}


def user_is_admin(user: User) -> bool:
    if user.is_superuser:
        return True
    return any(getattr(r, "code", None) == "admin" for r in (user.roles or []))


def _sort_menus(hrefs: Iterable[str]) -> list[str]:
    uniq = {h for h in hrefs if h}
    return sorted(uniq, key=lambda h: ALL_MENUS.index(h) if h in ALL_MENUS else 999)


def sanitize_role_menus(raw: Any) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            href = str(item or "").strip()
            if not href or href in seen:
                continue
            if href not in ALL_MENUS or href in ADMIN_ONLY_MENUS:
                continue
            seen.add(href)
            out.append(href)
    if "/" not in seen:
        out.insert(0, "/")
    return _sort_menus(out)


def menus_of_role(role: Any) -> list[str]:
    code = getattr(role, "code", None)
    if code == "admin":
        return list(ALL_MENUS)
    raw = getattr(role, "menus", None)
    if isinstance(raw, list) and raw:
        return sanitize_role_menus(raw)
    if code in SEED_ROLE_MENUS:
        return list(SEED_ROLE_MENUS[code])
    return ["/"]


def resolve_menus(user: User) -> list[str]:
    """返回用户可访问的菜单 href 列表。"""
    if user_is_admin(user):
        return list(ALL_MENUS)

    allowed: set[str] = set()
    for role in user.roles or []:
        if getattr(role, "code", None) == "admin":
            return list(ALL_MENUS)
        allowed.update(menus_of_role(role))
    allowed -= ADMIN_ONLY_MENUS
    if not allowed:
        return ["/"]
    return _sort_menus(allowed)


def can_access_menu(user: User, href: str) -> bool:
    menus = set(resolve_menus(user))
    if href in menus:
        return True
    for m in menus:
        if m != "/" and href.startswith(m + "/"):
            return True
    return False


TENDER_LIBRARY_HREF = "/tender-library"


def apply_tender_library_menu(menus: list[str], *, can_manage: bool) -> list[str]:
    kept = [h for h in menus if h != TENDER_LIBRARY_HREF]
    if not can_manage:
        return kept
    allowed = set(kept)
    allowed.add(TENDER_LIBRARY_HREF)
    return [h for h in ALL_MENUS if h in allowed]


async def resolve_menus_with_acl(db, user: User) -> list[str]:
    menus = resolve_menus(user)
    if user_is_admin(user):
        return apply_tender_library_menu(menus, can_manage=True)
    from api.services.knowledge.access import PERM_MANAGE, TENDER_LIB_PUBLIC_ID, perms_for_base
    from api.services.knowledge.bases import get_base_by_public_id
    from common.errors import AppError

    can_manage = False
    try:
        base = await get_base_by_public_id(db, TENDER_LIB_PUBLIC_ID)
        flags = await perms_for_base(db, user, base)
        can_manage = PERM_MANAGE in flags
    except AppError:
        can_manage = False
    return apply_tender_library_menu(menus, can_manage=can_manage)
