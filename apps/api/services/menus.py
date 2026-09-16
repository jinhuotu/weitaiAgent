"""菜单权限：按角色返回可访问的导航 href。"""

from __future__ import annotations

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

# 非管理员角色白名单（取并集）；未列出的角色默认「全站减去 adminOnly」
ROLE_MENU_ALLOW: dict[str, frozenset[str]] = {
    "operator": frozenset({"/", "/ai-chat", "/work-tasks", "/tenders", "/tender-library", "/quotes"}),
    "auditor": frozenset({"/", "/ai-chat", "/tender-tasks", "/approval", "/logs"}),
}


def user_is_admin(user: User) -> bool:
    if user.is_superuser:
        return True
    return any(getattr(r, "code", None) == "admin" for r in (user.roles or []))


def resolve_menus(user: User) -> list[str]:
    """返回用户可访问的菜单 href 列表。"""
    if user_is_admin(user):
        return list(ALL_MENUS)

    role_codes = {getattr(r, "code", None) for r in (user.roles or [])}
    role_codes.discard(None)

    restricted = [ROLE_MENU_ALLOW[c] for c in role_codes if c in ROLE_MENU_ALLOW]
    if restricted:
        allowed: set[str] = set()
        for s in restricted:
            allowed |= set(s)
        return sorted(allowed, key=lambda h: ALL_MENUS.index(h) if h in ALL_MENUS else 999)

    return [h for h in ALL_MENUS if h not in ADMIN_ONLY_MENUS]


def can_access_menu(user: User, href: str) -> bool:
    menus = set(resolve_menus(user))
    if href in menus:
        return True
    for m in menus:
        if m != "/" and href.startswith(m + "/"):
            return True
    return False
