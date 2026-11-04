"""投标文件格式章的截取规则。要放宽或收紧，只改这个文件。

截取只删范围外的正文块，范围内的段落、表格、图片保持上传 Word 的原 XML。
"""

from __future__ import annotations

import re
import zipfile
from io import BytesIO
from pathlib import Path

from lxml import etree

# 整段（去掉空白、去掉「第×章」）必须正好是其中一条，正文里顺带提到不算。
START_TITLES: tuple[str, ...] = (
    "投标文件格式",
    "响应文件格式",
    "投标文件组成",
    "响应文件组成",
)

# first=文档里第一次出现；last=最后一次（目录里常有同名短标题，默认用正文这次）
START_PICK = "last"

# 章名上限。超过的当成正文，不当作截取起止。
START_MAX_CHARS = 30
END_MAX_CHARS = 40

# 标题里出现这些，就不当成章名。
HEADING_BREAKS = "。；;"

_CHAPTER_PREFIX = re.compile(
    r"^第[一二三四五六七八九十百0-9]+(?:部分|章节|章|节)[、.．]?"
)
_XML_TOK = re.compile(
    r"<!--.*?-->|<!\[CDATA\[.*?\]\]>|<\?.*?\?>|</[\w:.-]+\s*>|<[\w:.-]+\b[^>]*?/?>",
    re.S,
)
_BODY_OPEN = re.compile(r"<((?:[\w.-]+:)?)body(\s[^>]*)?>")

SLICE_MARK = "docx-raw"
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _page_boundary(elements: list, index: int) -> bool:
    """只接受实际分页设置；空段、居中和渲染缓存本身不能证明独占一页。"""
    element = elements[index]
    nodes = list(element.iter())
    for i, node in enumerate(nodes):
        if node.tag == _W + "br" and node.get(_W + "type") == "page":
            if not any(n.tag == _W + "t" and (n.text or "").strip() for n in nodes[i + 1:]):
                return True
    if next(element.iter(_W + "sectPr"), None) is not None:
        # sectPr 描述它前面的节；断点之后是否另起页，由下一节的 type 决定。
        following = next((section for node in elements[index + 1:]
                          for section in node.iter(_W + "sectPr")), None)
        kind = following.find(_W + "type") if following is not None else None
        # 缺省分节类型为 nextPage，continuous/nextColumn 不构成分页。
        if kind is None or kind.get(_W + "val") in {"nextPage", "oddPage", "evenPage"}:
            return True
    return False


def _block_text(element) -> str:
    return "".join(n.text or "" for n in element.iter(_W + "t"))


def _has_artwork(element) -> bool:
    return any(next(element.iter(_W + name), None) is not None
               for name in ("drawing", "pict", "object", "tbl"))


def format_content_start(elements: list, start: int, end: int) -> int:
    """仅跳过独占一页的「第×章…文件格式」及其空白分页段，保留范本原 XML。"""
    title = elements[start]
    label = compact_heading(_block_text(title))
    if (title.tag != _W + "p" or not is_format_start(label)
            or re.match(r"^第[一二三四五六七八九十百0-9]+章[、.．]?(?:投标|响应)文件格式$", label) is None
            or _has_artwork(title)):
        return start
    if _page_boundary(elements, start):
        return start + 1
    for i in range(start + 1, end):
        element = elements[i]
        if element.tag != _W + "p" or _has_artwork(element):
            return start
        before = element.find(_W + "pPr/" + _W + "pageBreakBefore")
        if before is not None and before.get(_W + "val", "true") not in {"0", "false", "off"}:
            return i
        text = compact_heading(_block_text(element))
        # PDF 转档有时把印刷页码放进正文；它不算同页的封面范本内容。
        if text and re.fullmatch(r"-?\d{1,4}-?", text) is None:
            return start
        if _page_boundary(elements, i):
            return i + 1
    return start


def compact_heading(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _heading_body(compact: str) -> str:
    return _CHAPTER_PREFIX.sub("", compact)


def is_format_start(text: str) -> bool:
    compact = compact_heading(text)
    if not compact or len(compact) > START_MAX_CHARS:
        return False
    if any(ch in compact for ch in HEADING_BREAKS):
        return False
    return _heading_body(compact) in START_TITLES


def is_format_end(text: str) -> bool:
    """下一章的章名。仍是投标文件格式章时不在这里截断。"""
    compact = compact_heading(text)
    if not compact or len(compact) > END_MAX_CHARS:
        return False
    if any(ch in compact for ch in HEADING_BREAKS):
        return False
    if re.match(r"^第[一二三四五六七八九十百0-9]+章", compact) is None:
        return False
    return not is_format_start(compact)


def pick_start(indexes: list[int]) -> int | None:
    if not indexes:
        return None
    if START_PICK == "first":
        return indexes[0]
    return indexes[-1]


def cut_format_docx(src: Path, dest: Path) -> None:
    """按规则截出格式章。范围内的 XML 与上传文件一致。"""
    if not src.is_file():
        raise RuntimeError("未找到上传的 Word")
    raw = src.read_bytes()
    try:
        new_xml, name = _cut_document_xml(raw)
    except zipfile.BadZipFile as exc:
        raise RuntimeError("Word 无法打开") from exc
    dest.parent.mkdir(parents=True, exist_ok=True)
    buf = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as zin, zipfile.ZipFile(buf, "w") as zout:
        for item in zin.infolist():
            data = new_xml if item.filename == name else zin.read(item.filename)
            zout.writestr(item, data)
    dest.write_bytes(buf.getvalue())


def _cut_document_xml(blob: bytes) -> tuple[bytes, str]:
    with zipfile.ZipFile(BytesIO(blob)) as zf:
        name = _document_name(zf.namelist())
        xml = zf.read(name)
    text = xml.decode("utf-8")
    opened = _BODY_OPEN.search(text)
    if opened is None:
        raise RuntimeError("Word 正文缺失")
    prefix = opened.group(1)
    inner_at = opened.end()
    close = text.rfind(f"</{prefix}body>")
    if close < inner_at:
        raise RuntimeError("Word 正文不完整")
    inner = text[inner_at:close]
    spans = _top_spans(inner)
    if not spans:
        raise RuntimeError("Word 正文是空的")
    labels = [_elem_text(inner[a:b]) for a, b in spans]
    starts = [i for i, label in enumerate(labels) if is_format_start(label)]
    start = pick_start(starts)
    if start is None:
        raise RuntimeError("未找到投标文件格式章")
    end = len(spans)
    for i in range(start + 1, len(spans)):
        if _is_sect(inner[spans[i][0] : spans[i][1]]):
            end = i
            break
        if is_format_end(labels[i]):
            end = i
            break
    # 只用解析树判断边界，输出仍拼接上传文件的原始 XML 字节片段。
    root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True))
    body = root.find(_W + "body")
    elements = [node for node in body if isinstance(node.tag, str)] if body is not None else []
    if len(elements) == len(spans):
        start = format_content_start(elements, start, end)
    kept = spans[start:end]
    if not kept:
        raise RuntimeError("未找到投标文件格式章")
    sect = next((sp for sp in spans if _is_sect(inner[sp[0] : sp[1]])), None)
    parts = [inner[a:b] for a, b in kept]
    if sect is not None and sect not in kept:
        parts.append(inner[sect[0] : sect[1]])
    out = text[:inner_at] + "".join(parts) + text[close:]
    return out.encode("utf-8"), name


def _document_name(names: list[str]) -> str:
    for name in names:
        if name.lower() == "word/document.xml":
            return name
    raise RuntimeError("Word 正文缺失")


def _is_sect(raw: str) -> bool:
    head = raw.lstrip()[:80]
    return bool(re.match(r"</?(?:[\w.-]+:)?sectPr\b", head))


def _elem_text(raw: str) -> str:
    bits = re.findall(r"<[\w:.-]*:?t\b[^>]*>([^<]*)", raw)
    return "".join(bits)


def _top_spans(inner: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    pos = 0
    length = len(inner)
    while pos < length:
        while pos < length and inner[pos].isspace():
            pos += 1
        if pos >= length or inner.startswith("</", pos):
            break
        if inner.startswith("<!--", pos) or inner.startswith("<?", pos):
            tok = _XML_TOK.match(inner, pos)
            if tok is None:
                break
            pos = tok.end()
            continue
        if not inner.startswith("<", pos):
            nxt = inner.find("<", pos)
            pos = length if nxt < 0 else nxt
            continue
        start = pos
        depth = 0
        while True:
            tok = _XML_TOK.search(inner, pos)
            if tok is None or (depth == 0 and tok.start() != start):
                raise RuntimeError("格式章 XML 无法切分")
            piece = tok.group(0)
            pos = tok.end()
            if piece.startswith(("<?", "<!--", "<![")):
                continue
            if piece.startswith("</"):
                depth -= 1
                if depth == 0:
                    spans.append((start, pos))
                    break
                if depth < 0:
                    raise RuntimeError("格式章 XML 标签未闭合")
            elif piece.endswith("/>"):
                if depth == 0:
                    spans.append((start, pos))
                    break
            else:
                depth += 1
    return spans
