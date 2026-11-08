"""Read the evaluation schedule and attach traceable prose to existing sections."""

from dataclasses import dataclass, replace
import re
import unicodedata


_EVIDENCE_TERMS = ("证书", "执照", "证明", "扫描件", "截图", "合同", "报表", "报告", "许可证", "发票", "凭证", "资质文件", "缴税", "社保", "信用中国", "公示系统")
_NARRATIVE_TERMS = ("方案", "措施", "计划", "体系", "管理", "控制", "组织", "部署", "承诺", "建议", "理解", "分析", "目标", "安排", "程序", "说明", "流程", "构想", "对策")
_RESPONSE_SCHEME = ("承诺", "保证", "确保", "建立", "执行", "遵守", "实施", "满足", "方案", "措施", "计划", "体系", "管理", "控制", "组织", "部署", "安排")
_RESPONSE_PROOF = ("提供", "附", "证明", "扫描件", "证书", "业绩", "财务报表", "营业执照", "合同", "发票", "许可证", "缴税凭证", "编号")
_RESPONSE_REF = ("人员", "证书", "业绩", "资质", "证明")
_STOP_WORDS = set("的与及和或按根据本项目我方应当必须进行相关以及等其中要求" )


def score_item_kind(item_name: str, criterion: str) -> str:
    """Classify a score item before choosing evidence or narrative generation."""
    text = f"{item_name or ''}{criterion or ''}"
    evidence = any(term in text for term in _EVIDENCE_TERMS)
    narrative = any(term in text for term in _NARRATIVE_TERMS)
    if evidence and narrative:
        return "mixed"
    if evidence:
        return "evidence"
    return "narrative"


def classify_response(item_name: str, criterion: str, chapter: str = "") -> str:
    """Route scoring prose: declarations are the safe default."""
    text = f"{item_name or ''}{criterion or ''}"
    if "服务承诺" in chapter:
        return "declare"
    scheme = any(term in text for term in _RESPONSE_SCHEME)
    proof = any(term in item_name for term in _RESPONSE_PROOF)
    reference = any(term in text for term in _RESPONSE_REF)
    if proof and not scheme:
        return "provide_only"
    if scheme and reference:
        return "declare_with_ref"
    return "declare"


def criterion_terms(text: str) -> list[str]:
    compact = re.sub(r"[\d①②③④⑤⑥⑦⑧⑨⑩一二三四五六七八九十]+(?:[、.)）]|分)?", " ", text or "")
    terms = re.findall(r"[一-龥]{2,}|[A-Za-z]{2,}", compact)
    return list(dict.fromkeys(term for term in terms if term not in _STOP_WORDS))


def validate_declaration(text: str, terms: list[str], facts: object) -> dict:
    body = text or ""
    fact_values = [str(value) for value in (facts.values() if isinstance(facts, dict) else facts or []) if value not in (None, "")]
    hits = {term: term in body for term in terms}
    numbers = re.findall(r"\d+(?:\.\d+)?\s*(?:小时|天|次|级|元|人|座|%|日历天)?", body)
    quantitative = bool(numbers) or bool(re.search(r"每周|每月|节点|频次|阈值|响应时间|验收", body))
    third_person = any(term in body for term in ("承包人应", "投标人须"))
    coverage = sum(hits.values()) / len(hits) if hits else 1.0
    result = {"passed": coverage >= .8 and any(value in body for value in fact_values) and quantitative and body.count("我方") >= 3 and not third_person,
              "coverage": coverage, "missing_terms": [term for term, hit in hits.items() if not hit],
              "fact_hits": [value for value in fact_values if value in body], "quantitative": quantitative,
              "first_person_count": body.count("我方"), "third_person": third_person}
    return result


@dataclass(frozen=True)
class ScoringPoint:
    clause_no: str
    group: str
    group_score: int | None
    item: str
    item_score_min: int | None
    item_score_max: int | None
    criterion: str
    source_block: int
    target_section: str = ""


_CHAPTER = re.compile(r"^第[一二三四五六七八九十百0-9]+章")
_SCORE = re.compile(
    r"(?:[（(]\s*(?P<wrapped>\d+)\s*分\s*[）)]|"
    r"(?<![\d.．\-－–—~～至(（])(?:(?P<zero>0)\s*[-－–—~～至]\s*)?(?P<plain>\d+)\s*分)\s*$"
)
_CLAUSE = re.compile(
    r"(?:\d+(?:\.\d+)*\s*(?:[（(]\d+[）)])?|"
    r"[（(][一二三四五六七八九十0-9]+[）)]|\d+[.、]\d+(?:\.\d+)*)"
)
_EVALUATION = re.compile(
    r"(?:第[一二三四五六七八九十百0-9]+章)?(?:评标|评审)办法(?:[（(][^）)]+[）)])?"
)
_TOTAL_ENTRY = re.compile(r"(?:^|[；;])\s*(?:[（(][一二三四五六七八九十0-9]+[）)])?"
                          r"\s*([^：:；;]+?)[：:]\s*(\d+)\s*分")


def _score(text):
    match = _SCORE.search(text)
    if not match or re.search(r"[\-－–—~～至.．]\s*$", text[:match.start()]):
        return text.strip(), None, None
    return text[:match.start()].strip(), 0 if match["zero"] else None, int(match["wrapped"] or match["plain"])


def _item_name(text):
    return re.sub(r"^\s*(?:[（(]\d+[）)]|\d+[.、])\s*", "", text).strip()


def _title(text):
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"^\s*(?:\(?\d+\)|[一二三四五六七八九十]+、|\d+[.、])\s*", "", text)
    return re.sub(r"\s+", "", text).removesuffix("及附录")


def read_scoring_points(invitation, scoring_result=None, *, blocks=None, notes=None):
    """Use the source schedule as authority. Optional rows can confirm literal criteria."""
    notes = [] if notes is None else notes
    if blocks is None:
        blocks = ((i, line.strip(), [line.split("|")] if "|" in line else [])
                  for i, line in enumerate(invitation.splitlines()))
    extracted_scores = {row["requirement"].strip(): row.get("maxScore")
                        for row in (scoring_result or {}).get("rows", [])
                        if isinstance(row, dict) and isinstance(row.get("requirement"), str)
                        and isinstance(row.get("maxScore"), (int, float))}
    active = False
    totals = {}
    points = []
    clause = group = subgroup = ""
    group_score = None
    nested = None
    group_points = []

    def flush_nested():
        nonlocal nested
        if nested:
            parent, children = nested
            declared = parent.item_score_max
            scores = [point.item_score_max for point in children]
            if not children or (len(children) == 1 and parent.criterion):
                group_points.append(parent)
            else:
                group_points.extend(children)
                if None not in scores and sum(scores) != declared:
                    notes.append(f"评分子组{parent.item}原表{declared}分，细项合计{sum(scores)}分")
            nested = None

    def flush_group():
        flush_nested()
        if any(point.item_score_max is not None for point in group_points):
            points.extend(group_points)
        group_points.clear()

    for number, heading, rows in blocks:
        compact = re.sub(r"\s+", "", heading)
        if not rows:
            if _EVALUATION.fullmatch(compact):
                active = True
            elif active and _CHAPTER.match(compact):
                break
        if not active:
            continue
        for row in rows:
            cells = [cell.strip() for cell in row]
            if len(cells) != 4:
                continue
            number_cell, group_cell, item_cell, criterion = cells
            entries = _TOTAL_ENTRY.findall(criterion)
            if len(entries) >= 2:
                flush_group()
                clause = group = subgroup = ""
                for name, total in entries:
                    totals[name.strip()] = int(total)
                continue
            found = bool(_CLAUSE.fullmatch(number_cell))
            row_clause = unicodedata.normalize("NFKC", number_cell)
            name, _, declared = _score(group_cell)
            group_heading = bool("评分标准" in group_cell or re.search(r"[（(]\s*\d+\s*分\s*[）)]$", group_cell))
            child_number = bool(re.fullmatch(r"[（(][一二三四五六七八九十0-9]+[）)]", number_cell))
            if group_heading and (name.removesuffix("评分标准") != group or (found and not child_number and row_clause != clause)):
                flush_group()
                clause = row_clause if found else ""
                group = name.removesuffix("评分标准").strip()
                group_score = totals.get(group, declared)
                if declared is not None and group in totals and declared != totals[group]:
                    notes.append(f"评分组{group}分值组成{totals[group]}分，评分表{declared}分")
                subgroup = ""
            elif (group_cell and not group_heading) or (number_cell and not child_number and row_clause != clause):
                flush_group()
                clause = group = subgroup = ""
            if not group or "报价" in group or not (item_cell or criterion):
                continue
            item, item_min, item_max = _score(item_cell)
            detail, detail_min, detail_max = _score(criterion)
            if item_cell and not (nested and nested[0].item == _item_name(item).removesuffix("评分标准")):
                flush_nested()
                if item.endswith("评分标准") and not re.match(r"^\d+、", item) and detail_max is not None:
                    subgroup = item.removesuffix("评分标准")
                elif item_max is not None and item_min is None and re.search(
                    r"[（(]\s*\d+\s*分\s*[）)]$", item_cell
                ):
                    parent = ScoringPoint(clause, group, group_score, _item_name(item).removesuffix("评分标准"),
                                          detail_min, item_max, criterion, number)
                    nested = (parent, [])
            if nested:
                name = _item_name(detail)
                if name:
                    point = ScoringPoint(clause, group, group_score, name, detail_min, detail_max,
                                         criterion, number)
                    nested[1].append(point)
                continue
            original = criterion or item_cell
            name = _item_name(detail if subgroup or not item else item)
            if not name:
                continue
            minimum, maximum = (detail_min, detail_max) if detail_max is not None else (item_min, item_max)
            if maximum is not None and original in extracted_scores and extracted_scores[original] != maximum:
                notes.append(f"{clause} {name}：原表{maximum}分，已有抽取结果{extracted_scores[original]}分")
            group_points.append(ScoringPoint(clause, subgroup or group, group_score, name, minimum,
                                             maximum, original, number))
    flush_group()
    for name in dict.fromkeys(point.group for point in points):
        rows = [point for point in points if point.group == name]
        maxima = [point.item_score_max for point in rows]
        expected = rows[0].group_score
        if expected is not None and None not in maxima and sum(maxima) != expected:
            notes.append(f"评分组{name}原表{expected}分，子项合计{sum(maxima)}分")
    return points


def title_similarity(left, right):
    left, right = _title(left), _title(right)
    if not left or not right:
        return 0
    if left == right:
        return 1
    grams = lambda text: {text[i:i + 2] for i in range(len(text) - 1)}
    a, b = grams(left), grams(right)
    overlap = len(a & b) / max(1, len(a | b))
    if (left in right or right in left) and abs(len(left) - len(right)) <= 4:
        return max(0.7, overlap)
    return overlap if overlap >= 0.7 else 0


def match_scoring_sections(points, sections, notes):
    """Sections carry their literal heading and an optional source-written scoring cue."""
    groups = dict.fromkeys(point.group for point in points)
    candidates = []
    for group in groups:
        for title, cue in sections:
            score = max(title_similarity(group, title), title_similarity(group, cue))
            if score:
                candidates.append((score, group, title))
    matches = {}
    used = set()
    for _, group, title in sorted(candidates, key=lambda row: -row[0]):
        if group in matches or title in used:
            note = f"章节与评分组存在多义：{title} / {group}"
            if note not in notes:
                notes.append(note)
            continue
        matches[group] = title
        used.add(title)
    for group in (name for name in groups if name not in matches):
        total = next(point.group_score for point in points if point.group == group)
        notes.append(f"评分组{group}（{total if total is not None else '分值未标注'}分）在目录中无对应章节")
    return [replace(point, target_section=matches.get(point.group, "")) for point in points]


def _score_label(point):
    if point.item_score_max is None:
        return "分值未标注"
    if point.item_score_min is None:
        return f"{point.item_score_max} 分"
    return f"{point.item_score_min}-{point.item_score_max} 分"


def _narrative_for_point(point, *, with_reference: bool = False) -> str:
    """Write a distinct four-part declaration from the scoring point itself."""
    terms = criterion_terms(point.criterion or point.item)
    focus = "、".join(terms[:6]) or point.item
    variants = (
        "我方将先核对项目边界、接口条件和交付要求，再按责任界面组织实施；我方已建立过程记录、复核确认和问题销项机制。",
        "我方围绕项目目标拆分工作包，明确前置条件、检查节点和成果标准；我方承诺将意见闭环到责任人和交付物。",
        "我方将把关键风险纳入计划管理，按照识别、预防、检查、纠偏的顺序推进；我方以可追溯记录保证措施落地。",
        "我方承诺结合现场条件优化组织安排，持续复核进度、质量和接口状态；我方发现偏差即启动纠偏并完成复盘。",
    )
    variant = variants[sum(ord(ch) for ch in point.item) % len(variants)]
    stems = (
        f"本项目的{point.item}，我方依据工程范围、工期、质量标准和安全要求承接工作。",
        f"结合本项目现场边界与交付目标，我方将{point.item}作为主线安排资源和接口。",
        f"针对本项目的{point.item}，我方先核定条件，再组织专业协同和成果确认。",
        f"本项目推进{point.item}时，我方以可执行、可检查、可追溯作为基本要求。",
    )
    bodies = (
        f"我方围绕{focus}建立{point.item}专用台账，逐项登记输入、责任界面与交付结果；{variant}",
        f"我方把{point.item}拆为前置核验、过程控制、阶段复查和收口移交，针对{focus}设置问题登记与销项链条。",
        f"我方对{point.item}采用节点记录、周度复盘和阶段验收，遇到{focus}相关接口变化时同步说明影响并更新成果。",
        f"我方承诺以{point.item}作为验收重点，围绕{focus}持续协调、检查和改进，使承诺内容与本项目实际条件保持一致。",
    )
    offset = sum(ord(ch) for ch in point.item) % len(bodies)
    signature = f"专项安排围绕{point.item}展开：{focus}、{point.item}、{focus}分别对应准备、实施与验收，相关记录均以{point.item}为索引。"
    text = "\n\n".join((stems[offset % len(stems)], bodies[offset], bodies[(offset + 1) % len(bodies)], bodies[(offset + 2) % len(bodies)] + signature))
    if with_reference:
        text += "\n相关人员证书及证明材料见本章资格审查资料。"
    return text


def section_blocks(points, notes, *, cited_text=None):
    """Render each scoring point as a declaration or a cited excerpt."""
    result = []
    pending = 0
    for point in points:
        label = f"（对应评分项 {point.item}，{_score_label(point)}）"
        commitment = re.search(r"承诺|不拖欠|质保期(?:内|外)|协调.{0,15}周边|与招标人的配合",
                               point.group + point.criterion)
        if commitment:
            result.append(f"{label}\n{_narrative_for_point(point)}")
        else:
            citation = cited_text(point) if cited_text else None
            if citation:
                excerpt, source = citation
                result.append(f"{label}\n{excerpt}\n依据：{source}")
                continue
            result.append(f"{label}\n{_narrative_for_point(point, with_reference=score_item_kind(point.item, point.criterion) == 'mixed')}")
    return result


def word_scoring_blocks(doc):
    """Keep original Word body offsets and physical table boundaries."""
    from docx.oxml.ns import qn

    def text(el):
        return "".join(node.text or "" for node in el.iter(qn("w:t"))).strip()

    for i, el in enumerate(doc.element.body):
        if el.tag == qn("w:tbl"):
            yield i, "", [[text(cell) for cell in row.findall(qn("w:tc"))]
                          for row in el.findall(qn("w:tr"))]
        elif el.tag == qn("w:p"):
            yield i, text(el), []


_ENTITY_SCORE_TERMS = re.compile(
    r"人员|姓名|性别|年龄|职务|身份证|职称|证书|项目经理|负责人|法人|代表|公司|企业|单位|住所|地址|电话|传真|网址|邮编|成立|营业期限|注册资本|信用代码|资质|业绩|财务|净资产|营收"
)
_EXCLUDED_CHAPTER = re.compile(r"^第[一二三四五六七八九十0-9]+章\s*(?:评标|评审)办法")
_FORMAT_CHAPTER = re.compile(r"^第[一二三四五六七八九十0-9]+章\s*(?:投标|响应)文件格式")


def _citation_grams(text: str) -> set[str]:
    compact = re.sub(r"\s+", "", str(text or ""))
    return {compact[i:i + 2] for i in range(max(0, len(compact) - 1))}


def _citation_overlap(left: str, right: str) -> float:
    a, b = _citation_grams(left), _citation_grams(right)
    return len(a & b) / max(1, len(a | b))


def _fact_records(value):
    if isinstance(value, dict):
        value = value.get("clauses", value.get("facts", value))
    if isinstance(value, dict):
        rows = []
        for key, item in value.items():
            if isinstance(item, dict):
                name = str(item.get("name") or key)
                text = str(item.get("value") or "").strip()
                clause = str(key)
            else:
                name, text, clause = str(key), str(item or "").strip(), str(key)
            if text:
                rows.append((clause, name, text))
        return rows
    rows = []
    for item in value or ():
        if not isinstance(item, dict):
            continue
        clause = str(item.get("clause") or item.get("clause_no") or item.get("number") or "")
        name = str(item.get("name") or item.get("field") or item.get("label") or "")
        text = str(item.get("value") or item.get("text") or "").strip()
        if text:
            rows.append((clause, name, text))
    return rows


def source_citation(point, blocks, uploaded=(), library_facts=()):
    """Find a literal source excerpt using facts, full-block search, then uploads."""
    def item_value(text):
        match = re.match(r"^" + re.escape(point.item) + r"\s*[:：]\s*(.+)$", text.strip())
        if match and not re.search(r"我方承诺|本公司保证", match[1]):
            return match[1]
        return ""

    query = f"{point.item} {point.criterion}"
    entity_only = bool(_ENTITY_SCORE_TERMS.search(query))

    if not entity_only:
        best = None
        for clause, name, value in _fact_records(library_facts):
            score = max(_citation_overlap(query, name), _citation_overlap(point.item, name))
            if score >= 0.5 and (best is None or score > best[0]):
                best = (score, clause, value)
        if best is not None:
            return best[2], f"【招标原文·前附表 {best[1]}】"

    chapter_names = [str(heading).strip() for _, heading, _ in blocks if _CHAPTER.match(str(heading).strip())]
    excluded_found = any(_EXCLUDED_CHAPTER.match(name) or _FORMAT_CHAPTER.match(name) for name in chapter_names)
    active_chapter = ""
    placeholder_count: dict[str, int] = {}
    for _, heading, _ in blocks:
        heading = str(heading).strip()
        if _CHAPTER.match(heading):
            active_chapter = heading
        if any(token in heading for token in ("电子签章", "（盖单位章", "（签字）")):
            placeholder_count[active_chapter] = placeholder_count.get(active_chapter, 0) + 1

    active_chapter = ""
    if not entity_only:
        for number, heading, rows in blocks:
            heading = str(heading).strip()
            if _CHAPTER.match(heading):
                active_chapter = heading
            if excluded_found and (_EXCLUDED_CHAPTER.match(active_chapter) or _FORMAT_CHAPTER.match(active_chapter)):
                continue
            if not excluded_found and active_chapter and placeholder_count.get(active_chapter, 0) < 3:
                continue
            texts = [heading, *("：".join(str(cell) for cell in row if str(cell).strip()) for row in rows)]
            for text in texts:
                if len(text.strip()) < 60 or max(
                    _citation_overlap(point.item, text),
                    _citation_overlap(point.criterion, text),
                ) < 0.5:
                    continue
                if not re.search(r"我方承诺|本公司保证", text):
                    return text.strip(), f"【招标原文·原版块 {number}】"

    for record in uploaded:
        if record.subject_match and (value := item_value(record.text)):
            return value, record.marker()
    for fact in library_facts or ():
        if not isinstance(fact, dict) or not fact.get("fileId"):
            continue
        for field in fact.get("fields") or ():
            if not isinstance(field, dict):
                continue
            if value := item_value(f"{field.get('name', '')}：{field.get('value', '')}"):
                return value, f"【资料库·{fact['fileId']}】"
    return None

def render_section_paragraphs(doc, brief, format_path, slice_kinds, citations, *, toc=()):
    """Plan insertions into existing sections without mutating the Word document."""
    from docx import Document
    from docx.oxml.ns import qn
    from api.services.tenders.assets import load_invitation_text

    def _paragraph_text(el):
        return "".join(node.text or "" for node in el.iter(qn("w:t")))

    notes = []
    original = format_path.parent / f"{brief.invitationId}.docx"
    blocks = list(word_scoring_blocks(Document(original))) if original.is_file() else []
    points = read_scoring_points(load_invitation_text(brief.invitationId), blocks=blocks or None, notes=notes)
    if not points:
        return [], notes
    toc = set(toc)
    top = re.compile(r"^\s*[一二三四五六七八九十]+、")
    anchors = {}
    cues = []
    for el in doc.element.body:
        if el in toc or el.tag != qn("w:p"):
            continue
        title = _paragraph_text(el).strip()
        if not top.match(title) or len(title) > 60:
            continue
        following = el.getnext()
        following_text = _paragraph_text(following).strip() if following is not None and following.tag == qn("w:p") else ""
        cue = re.fullmatch(r"(?:请)?根据[“「]?(.+?)[”」]?评分标准(?:进行)?编制[。.]?", following_text)
        anchors[title] = following if cue else el
        cues.append((title, cue[1] if cue else ""))
    points = match_scoring_sections(points, cues, notes)
    for group in dict.fromkeys(p.group for p in points if not p.target_section):
        source = next(((number, heading) for number, heading, _ in reversed(blocks)
                       if top.match(heading) and title_similarity(group, heading)), None)
        if source is None:
            continue
        number, heading = source
        item = next((i for i in brief.outlineItems if title_similarity(heading, i.title) == 1), None)
        if item is not None and item.skipped:
            reason = getattr(item, "skipReason", "") or "大纲标记 skipped=True，未提供原因"
            notes.append(f"{heading}：跳过，{reason}")
        elif slice_kinds.get(heading) == "tech":
            notes.append(f"{heading}：归卷：技术标")
        elif item is None:
            notes.append(f"{heading}：大纲丢失：原版块 {number} 存在但未进入大纲")

    uploaded = []
    if brief.techPlanSourceIds:
        from api.services.tenders.gen_tech_plan import build_source_index

        uploaded = build_source_index(brief.techPlanSourceIds, brief=brief, notes=notes)
    texts = []
    for title, anchor in anchors.items():
        selected = [point for point in points if point.target_section == title]
        if not selected:
            continue
        if anchor.getnext() is not None and _paragraph_text(anchor.getnext()).startswith("（对应评分项 "):
            continue
        rendered = section_blocks(selected, notes, cited_text=lambda point: source_citation(
            point, blocks, uploaded, citations,
        ))
        texts.extend((anchor, text) for text in rendered)
    return texts, notes
