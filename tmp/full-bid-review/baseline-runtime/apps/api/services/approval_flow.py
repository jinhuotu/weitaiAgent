"""投标审批流程定义：步骤绑定用户与权限中的角色。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from common.errors import AppError, ErrorCode
from common.times import to_epoch_ms
from db.models.approval_flow import ApprovalFlow
from db.models.role import Role
from db.models.user import User

FLOW_CODE_TENDER = "tender"
KIND_START = "start"
KIND_REVIEW = "review"
KIND_END = "end"
KEY_SUBMIT = "submit"
KEY_DONE = "done"
NAME_SUBMIT = "提交申请"
NAME_DONE = "完成"

DEFAULT_REVIEW_STEPS: tuple[dict[str, str | None], ...] = (
    {"key": "review_dept", "name": "部门经理审批", "roleCode": None},
    {"key": "review_gm", "name": "总经理审批", "roleCode": None},
    {"key": "review_finance", "name": "财务审核", "roleCode": None},
)


def default_steps() -> list[dict[str, Any]]:
    reviews = [
        {
            "key": item["key"],
            "name": item["name"],
            "kind": KIND_REVIEW,
            "roleCode": item["roleCode"],
        }
        for item in DEFAULT_REVIEW_STEPS
    ]
    return _wrap_bookends(reviews)


def _wrap_bookends(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": KEY_SUBMIT,
            "name": NAME_SUBMIT,
            "kind": KIND_START,
            "roleCode": None,
            "roleName": None,
        },
        *reviews,
        {
            "key": KEY_DONE,
            "name": NAME_DONE,
            "kind": KIND_END,
            "roleCode": None,
            "roleName": None,
        },
    ]


def _clean_text(value: object, *, limit: int) -> str:
    return str(value or "").strip()[:limit]


def normalize_steps(raw: object) -> list[dict[str, Any]]:
    if isinstance(raw, dict):
        raw = raw.get("steps")
    if not isinstance(raw, list) or not raw:
        return default_steps()
    reviews: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        kind = _clean_text(item.get("kind"), limit=16) or KIND_REVIEW
        if kind != KIND_REVIEW:
            continue
        key = _clean_text(item.get("key"), limit=32)
        if not key or key in {KEY_SUBMIT, KEY_DONE} or key in seen:
            key = f"rev_{uuid4().hex[:12]}"
        seen.add(key)
        name = _clean_text(item.get("name"), limit=16) or "审批"
        role_code = _clean_text(item.get("roleCode") or item.get("role_code"), limit=64)
        role_name = _clean_text(item.get("roleName") or item.get("role_name"), limit=64)
        reviews.append(
            {
                "key": key,
                "name": name,
                "kind": KIND_REVIEW,
                "roleCode": role_code or None,
                "roleName": role_name or None,
            }
        )
    if not reviews:
        return default_steps()
    return _wrap_bookends(reviews)


def review_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in steps if item.get("kind") == KIND_REVIEW]


def step_names(steps: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("name") or "") for item in steps]


def find_step(steps: list[dict[str, Any]], token: str | None) -> dict[str, Any] | None:
    raw = (token or "").strip()
    if not raw:
        return None
    for item in steps:
        if item.get("key") == raw or item.get("name") == raw:
            return item
    return None


def display_step_name(steps: list[dict[str, Any]] | None, token: str | None) -> str:
    raw = (token or "").strip()
    if not raw:
        return ""
    if not steps:
        return raw
    found = find_step(steps, raw)
    if found:
        return str(found.get("name") or raw)
    return raw


def snapshot_from_steps(steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {"steps": normalize_steps(steps)}


def steps_from_snapshot(snapshot: object) -> list[dict[str, Any]] | None:
    if not isinstance(snapshot, dict):
        return None
    raw = snapshot.get("steps")
    if not isinstance(raw, list) or not raw:
        return None
    return normalize_steps(raw)


def next_step_on_pass(
    current_step: str,
    steps: list[dict[str, Any]] | None = None,
) -> tuple[str, str]:
    """返回 (status, current_step_key)。最后一审通过则 approved + 完成。"""
    chain = review_steps(steps or default_steps())
    if not chain:
        raise AppError(ErrorCode.VALIDATION, "审批流程至少需要一个审批环节", status_code=422)
    token = (current_step or "").strip() or str(chain[0]["key"])
    idx = next(
        (i for i, item in enumerate(chain) if item["key"] == token or item["name"] == token),
        0,
    )
    if idx >= len(chain) - 1:
        return "approved", KEY_DONE
    return "pending", str(chain[idx + 1]["key"])


def user_role_codes(user: User) -> set[str]:
    codes: set[str] = set()
    for role in user.roles or []:
        code = str(getattr(role, "code", "") or "")
        if code:
            codes.add(code)
    return codes


def user_can_act_on_step(user: User, step: dict[str, Any] | None) -> bool:
    if getattr(user, "is_superuser", False):
        return True
    if not step or step.get("kind") != KIND_REVIEW:
        return False
    role_code = _clean_text(step.get("roleCode"), limit=64)
    if not role_code:
        return True
    return role_code in user_role_codes(user)


def user_can_act_on_record(user: User, snapshot: object, current_step: str | None) -> bool:
    steps = steps_from_snapshot(snapshot) or default_steps()
    step = find_step(steps, current_step)
    if step is None:
        reviews = review_steps(steps)
        step = reviews[0] if reviews else None
    return user_can_act_on_step(user, step)


async def _role_map(db: AsyncSession, codes: set[str]) -> dict[str, str]:
    cleaned = {code for code in codes if code}
    if not cleaned:
        return {}
    rows = (await db.execute(select(Role).where(Role.code.in_(cleaned)))).scalars().all()
    return {str(row.code): str(row.name) for row in rows}


async def _with_role_names(db: AsyncSession, steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    names = await _role_map(
        db,
        {str(item.get("roleCode") or "") for item in steps},
    )
    out: list[dict[str, Any]] = []
    for item in steps:
        copied = dict(item)
        code = str(copied.get("roleCode") or "")
        copied["roleName"] = names.get(code) if code else None
        out.append(copied)
    return out


def public_steps(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "key": item.get("key"),
            "name": item.get("name"),
            "kind": item.get("kind"),
            "roleCode": item.get("roleCode"),
            "roleName": item.get("roleName"),
        }
        for item in steps
    ]


async def get_tender_flow(db: AsyncSession, *, can_edit: bool) -> dict[str, Any]:
    row = (
        await db.execute(select(ApprovalFlow).where(ApprovalFlow.code == FLOW_CODE_TENDER))
    ).scalar_one_or_none()
    steps = normalize_steps(row.steps if row is not None else None)
    labeled = await _with_role_names(db, steps)
    updated_at = getattr(row, "updated_at", None) if row is not None else None
    return {
        "code": FLOW_CODE_TENDER,
        "canEdit": can_edit,
        "steps": public_steps(labeled),
        "updatedAt": to_epoch_ms(updated_at) if updated_at else None,
    }


async def save_tender_flow(
    db: AsyncSession,
    *,
    user: User,
    reviews: list[dict[str, Any]],
) -> dict[str, Any]:
    cleaned = [
        {
            "key": item.get("key"),
            "name": item.get("name"),
            "kind": KIND_REVIEW,
            "roleCode": item.get("roleCode") or item.get("role_code"),
        }
        for item in reviews
    ]
    steps = normalize_steps(cleaned)
    chain = review_steps(steps)
    if not chain:
        raise AppError(ErrorCode.VALIDATION, "至少保留一个审批环节", status_code=422)
    codes = {str(item.get("roleCode") or "") for item in chain if item.get("roleCode")}
    if codes:
        existing = set(await _role_map(db, codes))
        missing = sorted(codes - existing)
        if missing:
            raise AppError(
                ErrorCode.VALIDATION,
                "角色不存在：" + "、".join(missing),
                status_code=422,
            )
    row = (
        await db.execute(select(ApprovalFlow).where(ApprovalFlow.code == FLOW_CODE_TENDER))
    ).scalar_one_or_none()
    if row is None:
        row = ApprovalFlow(code=FLOW_CODE_TENDER, steps=steps, updated_by=int(user.id))
        db.add(row)
    else:
        row.steps = steps
        row.updated_by = int(user.id)
    await db.commit()
    await db.refresh(row)
    return await get_tender_flow(db, can_edit=True)
