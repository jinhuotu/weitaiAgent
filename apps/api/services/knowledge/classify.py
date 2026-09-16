"""知识库入库分类：规范/产品说明可检索；整份投标书与他司投标主体不进 RAG。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from api.services.tenders.history import BIDDER_LOCK, contains_foreign_bidder

TAG_BID_FOREIGN = "bid_foreign"

# 邀请书/须知/招标文件本身可以对照资格条款，不要当「投标书」丢掉。
_INVITE_NAME_SAFE = (
    "招标文件",
    "投标邀请",
    "邀请书",
    "投标人须知",
    "评标办法",
    "资格预审",
)
_BID_PACKAGE_NAME = ("投标函", "投标书", "投标文件")
_WEITAI = (BIDDER_LOCK, "河南伟泰", "伟泰光电")
_BIDDER_LINE = re.compile(r"投标人\s*[:：]\s*([^\n。；]{2,80})")
_NAME_NOISE = re.compile(r"[\s_\-—（）()【】\[\]\d.]+")
# 资格条款里常见「投标人：必须是…有限公司」，不能当成已填投标人。
_BIDDER_REQ_WORDS = (
    "必须",
    "应当",
    "应是",
    "应为",
    "须为",
    "须是",
    "具备",
    "具有",
    "注册",
    "资格",
    "境内",
    "独立法人",
    "企业",
    "条件",
    "义务",
    "须知",
    "提供",
    "提交",
    "满足",
)


@dataclass(frozen=True)
class KbClass:
    skip_vectorize: bool
    reason: str = ""


def has_skip_rag_tag(tags: list | tuple | None) -> bool:
    return any(str(t).strip() == TAG_BID_FOREIGN for t in (tags or []))


def apply_skip_rag_tag(tags: list | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in [*(tags or []), TAG_BID_FOREIGN]:
        label = str(raw).strip()
        if not label or label in seen:
            continue
        seen.add(label)
        out.append(label)
    return out


def name_looks_like_bid_package(name: str) -> bool:
    """文件名本身像整份投标书，而不是正文里提到「投标人」。"""
    stem = Path(name or "").stem
    if any(mark in stem for mark in _INVITE_NAME_SAFE):
        return False
    compact = _NAME_NOISE.sub("", stem)
    if not compact:
        return False
    for mark in _BID_PACKAGE_NAME:
        if compact == mark or compact.endswith(mark):
            return True
        if compact.startswith(mark):
            rest = compact[len(mark) :]
            if any(word in rest for word in ("编制", "说明", "须知", "格式", "目录", "装订")):
                continue
            return True
    return False


def _looks_like_filled_company(name: str) -> bool:
    n = (name or "").strip().split("（")[0].split("(")[0].strip()
    if len(n) < 6 or len(n) > 40:
        return False
    if any(word in n for word in _BIDDER_REQ_WORDS):
        return False
    return n.endswith("有限公司") or "股份有限公司" in n


def _other_company_bidders(text: str) -> list[str]:
    found: list[str] = []
    for match in _BIDDER_LINE.finditer(text or ""):
        name = (match.group(1) or "").strip()
        if not _looks_like_filled_company(name):
            continue
        if any(mark in name for mark in _WEITAI):
            continue
        if name not in found:
            found.append(name)
    return found


def classify_kb_document(
    *,
    name: str = "",
    text: str = "",
    tags: list | None = None,
) -> KbClass:
    # 投标资料库扫描件是公司自有资质/合同，必须进 RAG，不受「整份投标书」启发式影响
    if any(str(t).strip() == "投标资料" for t in (tags or [])):
        return KbClass(False, "")
    if name_looks_like_bid_package(name):
        return KbClass(True, "文件名像整份投标文件/投标函，已保留原件但不向量化，检索将跳过")
    blob = f"{name}\n{text or ''}"
    if contains_foreign_bidder(blob):
        return KbClass(True, "正文或文件名出现其他公司投标主体，不写入检索")
    others = _other_company_bidders(text or "")
    if others:
        return KbClass(True, "正文出现非伟泰投标人，不写入检索")
    return KbClass(False, "")


def should_skip_kb_retrieval(
    *,
    name: str = "",
    content: str = "",
    tags: list | None = None,
) -> bool:
    if has_skip_rag_tag(tags):
        return True
    return classify_kb_document(name=name, text=content, tags=tags).skip_vectorize
