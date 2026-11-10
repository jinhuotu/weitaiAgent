"""Read the evaluation schedule and attach traceable prose to existing sections."""

from dataclasses import dataclass, replace
import logging
import re
import unicodedata


_EVIDENCE_TERMS = ("证书", "执照", "证明", "扫描件", "截图", "合同", "报表", "报告", "许可证", "发票", "凭证", "资质文件", "缴税", "社保", "信用中国", "公示系统")
_NARRATIVE_TERMS = ("方案", "措施", "计划", "体系", "管理", "控制", "组织", "部署", "承诺", "建议", "理解", "分析", "目标", "安排", "程序", "说明", "流程", "构想", "对策")
_RESPONSE_SCHEME = ("承诺", "保证", "确保", "建立", "执行", "遵守", "实施", "满足", "方案", "措施", "计划", "体系", "管理", "控制", "组织", "部署", "安排")
_RESPONSE_PROOF = ("提供", "附", "证明", "扫描件", "证书", "业绩", "财务报表", "营业执照", "合同", "发票", "许可证", "缴税凭证", "编号")
_RESPONSE_REF = ("人员", "证书", "业绩", "资质", "证明")
_STOP_WORDS = set("的与及和或按根据本项目我方应当必须进行相关以及等其中要求" )
SCORING_DUP_JACCARD = 0.6  # 超过该值视为重复度偏高，便于暴露模板复制。
SCORING_SEVERE_JACCARD = 0.8  # 超过该值视为严重重复，仍只告警不中断生成。
logger = logging.getLogger("api.tenders.scoring")


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


def _clean_item(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "")).strip()
    return value.translate(str.maketrans("（）", "()"))


def _fact_sentences(facts: dict) -> list[str]:
    project = facts.get("项目名称")
    location = facts.get("建设地点")
    sentences = []
    if project or location:
        lead = "本项目"
        if project:
            lead += f"为{project}"
        if location:
            lead += f"，建设地点为{location}"
        sentences.append(lead + "。")
    for key in ("计划工期", "质量标准", "招标范围", "投标有效期", "投标保证金"):
        value = facts.get(key)
        if value in (None, ""):
            continue
        text = str(value).strip()
        if len(text) > 80:
            text = re.split(r"[；;。]", text, maxsplit=1)[0].strip()
        if key == "投标保证金":
            try:
                amount = float(text)
                if amount.is_integer():
                    text = f"人民币{int(amount) / 10000:g}万元"
            except (TypeError, ValueError):
                pass
        if key == "计划工期" and text.isdigit():
            text += "日历天"
        sentences.append(f"{key}为{text}。")
    return sentences


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


def _scoring_fact_text(facts: dict | None) -> str:
    return "".join(_fact_sentences(facts or {})) or "本项目按已核实的招标条件组织实施。"


def _criterion_elements(criterion: str, item: str) -> list[str]:
    """从评分细则按标点切出可执行要素，保持兜底段落的结构差异。"""
    raw = re.sub(r"\s+", "", str(criterion or item or ""))
    raw = re.sub(r"(?:0\s*[-－–—~～至]\s*)?\d+\s*分\s*$", "", raw)
    chunks = [part.strip(" ：:；;，,。！？!？、") for part in re.split(r"[；;。！？!？]|\n", raw)]
    chunks = [part for part in chunks if len(part) >= 2]
    if len(chunks) < 2:
        chunks = [part for part in re.split(r"[、，,：:]", raw) if len(part.strip()) >= 2]
    if not chunks:
        chunks = criterion_terms(raw) or [_clean_item(item)]
    merged: list[str] = []
    for chunk in chunks:
        chunk = chunk.strip(" ：:；;，,。！？!？、")
        if not chunk:
            continue
        # 形容词性尾巴不是独立评分要素；并入前一项，避免输出“可行：……”病句。
        if re.fullmatch(r"(?:且|并且)?(?:措施)?(?:合理|可行|先进|完整|齐全|明确|具体)+(?:的)?", chunk):
            if merged:
                merged[-1] = f"{merged[-1]}，{chunk.lstrip('且并且')}"
            continue
        merged.append(chunk)
    return list(dict.fromkeys(merged))[:8] or [_clean_item(item)]


def _criterion_action(element: str, index: int) -> str:
    """Turn a literal criterion element into a distinct executable arrangement."""
    value = element.strip(" ：:；;，,。！？!？、")
    value = re.sub(
        r"(?:[、，,]\s*)?(?:合理|可行|先进|完整|齐全|明确|具体)"
        r"(?:(?:[、，,]\s*)(?:合理|可行|先进|完整|齐全|明确|具体))*(?:的)?$",
        "",
        value,
    ).rstrip(" 、，,") or element.strip(" ：:；;，,。！？!？、")
    if "工资" in value:
        detail = (
            "由劳资负责人在每月计量后核对实名考勤和工资表，按期向工人本人支付并保存银行回执"
            if index % 2 == 0 else
            "由项目经理在发薪日前复核班组出勤、应发金额和付款账户，发现差异先纠正再支付"
        )
    elif "农忙" in value or "资金不到位" in value or "连续施工" in value:
        detail = (
            "由施工负责人提前排定农忙期间的替补班组，按关键工序倒排人员和材料进场"
            if "农忙" in value else
            "由财务负责人把关键工序所需支出列入独立资金计划，按周核对资金余额和支付节点"
        )
    elif "质保期" in value:
        detail = (
            "由服务负责人在质保期内登记报修、安排响应并回访修复结果"
            if "期内" in value else
            "由服务负责人在质保期外先确认故障原因和服务范围，再书面安排检修"
        )
    elif "村民" in value or "周边关系" in value or "当地政府" in value:
        detail = "把通行、噪声和施工时段列为现场沟通事项，记录意见与处理结果"
    elif "招标人" in value or "甲方" in value:
        detail = "与招标人确认设计交底、停送电协调和交接事项，按会议纪要追踪待办"
    elif "合理化建议" in value:
        detail = "提交建议时列明对工期、投资和运维的影响，取得确认后再纳入实施"
    elif "扬尘" in value or "环境" in value:
        detail = "布置车辆冲洗点，对裸土覆盖和运输遗撒分别巡查，记录整改前后的状态"
    elif "安全" in value:
        detail = "把危险源写入班前交底，发现隐患暂停相关作业，复查销项后恢复"
    elif "设备" in value or "安装" in value:
        detail = (
            "由安装负责人按设备清单逐台核对基础、接口和安装条件，形成开箱及定位记录"
            if index % 2 == 0 else
            "由调试负责人逐台检查接线、保护参数和试运转状态，异常项隔离后复测"
        )
    elif "设计质量" in value:
        detail = (
            "由专业设计负责人编制输入清单，校核人逐张比对接口，审定人确认版本后签认"
            if index % 2 == 0 else
            "由校核负责人汇总审查意见，图纸发出前逐项销项，不合格成果退回复审"
        )
    elif "施工方法" in value:
        detail = "在代表性作业面试做，核验操作顺序和检查结果后再推广"
    elif "进度" in value or "网络图" in value or "节点" in value:
        detail = (
            "在节点清单中标出设计交付物、会审参与方和前置输入，设计负责人在会审后确认关闭条件"
            if "节点" in value else
            "在进度表中标明前置工序、交接条件和完成标志，计划工程师每周更新关键路径"
            if index % 2 == 0 else
            "施工负责人在节点前核对实际完成量，出现偏差时提出赶工或顺延调整并留存审批"
        )
    elif "质量" in value:
        detail = (
            "由质量负责人组织首件确认，施工班组按样板记录工艺参数后展开作业"
            if index % 4 == 0 else
            "由质量员按批次抽检并登记结果，不合格项开具整改单，复查合格后销项"
            if index % 4 == 1 else
            "由技术负责人核对质量目标、操作依据和检查频次，形成阶段质量分析记录"
            if index % 4 == 2 else
            "由项目经理在验收前组织质量复核，确认遗留问题责任和关闭期限"
        )
    elif "限额" in value:
        detail = "按限额边界建立估算、校核和超限回退机制"
    elif "投资" in value or "成本" in value:
        detail = "按投资控制线核对输入、测算变化并记录审批"
    elif "合同" in value:
        detail = "按合同接口建立交底、变更和索赔留痕"
    elif "信息" in value or "资料" in value:
        detail = "按资料目录设置收集、校验、归档和追溯节点"
    elif "风险" in value:
        detail = (
            "由安全负责人按风险等级设置预警阈值和响应责任，班前会通报当日控制重点"
            if index % 4 == 0 else
            "由项目经理在事件闭环后组织复盘，更新风险清单、措施和责任人"
            if index % 4 == 1 else
            "由专业负责人定位风险控制要点，逐阶段核对预防措施和现场触发条件"
            if index % 4 == 2 else
            "由应急负责人演练响应路径，检查物资、联络和恢复步骤是否可执行"
        )
    elif "新技术" in value or "新材料" in value or "BIM" in value:
        detail = (
            "由技术负责人组织适用性评审，比较接口、成本和维护条件后形成选用意见"
            if index % 2 == 0 else
            "由专业负责人安排样板验证，记录试用结果并在推广前完成复盘和版本确认"
        )
    elif "人员" in value or "配备" in value or "机构" in value:
        detail = (
            "由项目经理按岗位职责编制接口清单，开工前完成到岗核验并登记替补安排"
            if index % 2 == 0 else
            "由综合负责人按周检查人员履职记录，岗位变更须经项目经理确认后更新名单"
        )
    elif "意图" in value or "难点" in value:
        detail = "结合现场约束列出设计冲突，比较可选方案并将采用理由写入会审纪要"
    elif "设计" in value:
        detail = (
            "由设计负责人在输入冻结后组织专题评审，校审意见逐条回填变更清单"
            if index % 2 == 0 else
            "由专业负责人在节点交付前核对图纸接口和计算依据，变更经确认后发布新版本"
        )
    elif "施工" in value or "工序" in value:
        detail = (
            "由施工负责人按工序拆分作业面，为前后班组列出交接检查和成品保护要求"
            if index % 2 == 0 else
            "由班组长在作业前复核材料、机具和作业条件，完成后交回检查记录"
        )
    elif "结算" in value:
        detail = "逐项对照签证、计量记录和合同单价，差异经双方确认后纳入结算"
    elif "移交" in value:
        detail = "按移交清单交付竣工图、操作资料和遗留事项，双方签收"
    elif "总体管理" in value:
        detail = "开工前编列工作界面与成果清单，明确设计交底、施工交接和竣工移交的分工"
    elif "目标" in value:
        detail = "将目标拆为设计成果、施工节点和交付条件，按阶段核对偏差"
    elif "体系" in value or "保证" in value:
        detail = "按目标、责任、审核三级建立清单"
    elif any(token in value for token in ("措施", "方案")):
        detail = (
            "由责任负责人在准备阶段确认输入和资源，实施阶段按清单推进，复核阶段关闭问题"
            if index % 2 == 0 else
            "由专业负责人编制作业卡，施工前交底、过程抽查、完成后签认并归档"
        )
    elif any(token in value for token in ("计划", "进度", "安排")):
        detail = "形成节点表并设置偏差回看点"
    elif any(token in value for token in ("标准", "要求", "验收")):
        detail = "建立逐条核对表并保留验收记录"
    elif any(token in value for token in ("理解", "意图", "难点")):
        detail = "先形成问题清单，再安排专题评审"
    else:
        details = (
            "先拆分输入与边界，再安排执行和验证",
            "设置责任人、检查点和留痕记录",
            "按现场条件形成动作清单并闭环复核",
            "建立前置核对、过程检查和结果确认",
        )
        detail = details[index % len(details)]
    records = (
        f"项目负责人当天签认{value if len(value) <= 24 else value[:20] + '事项'}清单",
        f"专业负责人每周复核{value if len(value) <= 24 else value[:20] + '事项'}台账",
        f"资料负责人阶段验收前归档{value if len(value) <= 24 else value[:20] + '事项'}版本",
        f"复核人关闭{value if len(value) <= 24 else value[:20] + '事项'}整改单后销项",
        f"技术负责人在会审纪要中确认{value if len(value) <= 24 else value[:20] + '事项'}",
        f"施工负责人在交接记录中标注{value if len(value) <= 24 else value[:20] + '事项'}",
        f"服务负责人回访后登记{value if len(value) <= 24 else value[:20] + '事项'}结果",
        f"项目经理在月度检查表中复核{value if len(value) <= 24 else value[:20] + '事项'}",
    )
    record = records[index % len(records)]
    return f"{value}：{detail}；{record}。"


def criterion_body(item: str, criterion: str, *, facts: dict | None = None) -> str:
    source = _item_name(_score(re.sub(r"\s+", "", criterion or item))[0])
    elements = _criterion_elements(source, item)
    lines = [
        *(
        _criterion_action(element, index)
        for index, element in enumerate(elements)
        ),
    ]
    all_elements = ''.join(elements)
    lead = elements[0][:16] if len(elements) > 1 else _clean_item(item)[:16]
    if len(elements) == 1:
        if '合理化建议' in all_elements or '建议' in all_elements:
            lead = '建议采纳结果'
        elif '招标人' in all_elements or '甲方' in all_elements:
            lead = '协作事项回执'
        elif '限额' in all_elements:
            lead = '限额边界成果'
        elif '风险' in all_elements or '安全' in all_elements:
            lead = '风险处置结果'
        else:
            lead = '本项成果'
    if '进度' in all_elements or '节点' in all_elements or '网络图' in all_elements:
        closing = f"以“{lead}”对应的节点完成日、前置关系和审批签认作为收口依据；实际日期晚于计划或交接资料缺项时，标记未完成并重新排程。"
    elif '质量' in all_elements or '验收' in all_elements:
        closing = f"以“{lead}”的检查结果、整改复查和验收签字作为收口依据；不合格项未关闭或记录缺页时，不得进入下一工序。"
    elif '风险' in all_elements or '安全' in all_elements:
        closing = f"以“{lead}”的预警处置、复盘结论和责任确认作为收口依据；风险仍处于开放状态时，维持控制措施并暂停放行。"
    elif '工资' in all_elements or '质保' in all_elements or '村民' in all_elements or '招标人' in all_elements:
        closing = f"以“{lead}”的付款、回访、沟通或协作回执作为收口依据；未取得签收或问题未闭环时，列入下次履约跟踪。"
    else:
        closing = f"以“{lead}”的成果版本、检查记录和负责人签认作为收口依据；任一要素缺项时退回补正，复核完成后再提交。"
    lines.append(closing)
    text = "\n".join(lines)
    if len(re.sub(r"\s+", "", text)) < 180:
        lines[-1] = lines[-1].rstrip("。") + f"，围绕{lead}由项目负责人在阶段例会上复核，资料员归档签认页和整改闭环，直到复核时间与结果记录完整，并在月度例会上复盘责任人签字，形成可追溯版本并注明复核日期。"
        text = "\n".join(lines)
    return text


async def generate_scoring_narrative(client, *, item, criterion, chapter, facts, score, instructions=None):
    prompt = (
        "本项目事实（只供理解，不在评分项正文复述）：\n" + str(facts)
        + "\n评分项：" + item + "\n评分细则原文：" + criterion
        + "\n所属章节：" + chapter + "\n分值：" + str(score)
        + "\n" + (instructions or (
            "直接写投标正文。按细则中的动宾要素逐句给出具体安排。保留原文中的关键动作和对象，"
            "说明执行或核验方式。篇幅随要素数量变化。不要重复标题、项目事实或评分提示，"
            "不得虚构人员、证书、业绩或数值。"
        ))
    )
    try:
        return str(await client.complete([{"role": "user", "content": prompt}], mode="pro") or "").strip()
    except Exception as exc:
        logger.warning("评分正文模型调用失败 item=%s error=%s", item, type(exc).__name__)
        return ""


def _narrative_for_point(
    point,
    *,
    facts=None,
    generator=None,
    with_reference: bool = False,
    include_facts: bool = True,
    metadata: dict | None = None,
) -> str:
    """Use an injected model generator, with a data-only generic fallback."""
    degraded = False
    if generator is not None:
        try:
            generated = generator(point, facts or {})
            if generated:
                if isinstance(generated, dict):
                    degraded = bool(generated.get("degraded"))
                    generated = generated.get("text") or ""
                body = str(generated).strip()
                if body:
                    if include_facts:
                        body = _scoring_fact_text(facts) + "\n\n" + body
                    if metadata is not None:
                        metadata.setdefault("items", []).append(
                            {"item": point.item, "degraded": degraded}
                        )
                    return body
        except Exception as exc:  # model failure falls through to deterministic prose
            logger.error(
                "评分项正文生成异常：item=%s exception=%s",
                point.item,
                type(exc).__name__,
            )
            degraded = True
    item = _clean_item(point.item)
    elements = _criterion_elements(point.criterion, item)
    lines = [_criterion_action(element, index) for index, element in enumerate(elements)]
    text = criterion_body(item, point.criterion, facts=facts)
    if include_facts:
        text = _scoring_fact_text(facts) + "\n\n" + text
    if with_reference:
        text += "\n相关人员证书及证明材料见本章资格审查资料。"
    if metadata is not None:
        metadata.setdefault("items", []).append({"item": point.item, "degraded": True})
    return text


def section_blocks(points, notes, *, cited_text=None, facts=None, generator=None, metadata=None):
    """Render each scoring point as a declaration or a cited excerpt."""
    result = []
    pending = 0
    for index, point in enumerate(points):
        include_facts = index == 0
        commitment = re.search(r"承诺|不拖欠|质保期(?:内|外)|协调.{0,15}周边|与招标人的配合",
                               point.group + point.criterion)
        if commitment:
            result.append(_narrative_for_point(point, facts=facts, generator=generator,
                                               include_facts=include_facts, metadata=metadata))
        else:
            citation = cited_text(point) if cited_text else None
            if citation:
                excerpt, source = citation
                result.append(f"{excerpt}\n依据：{source}")
                if metadata is not None:
                    metadata.setdefault("items", []).append({"item": point.item, "degraded": False})
                continue
            result.append(_narrative_for_point(
                point,
                facts=facts,
                generator=generator,
                with_reference=score_item_kind(point.item, point.criterion) == 'mixed',
                include_facts=include_facts,
                metadata=metadata,
            ))
    return result


def scoring_uniqueness(texts: list[str]) -> dict:
    """Return pairwise character 2-gram overlap and common-prefix diagnostics."""
    def normalize(value: str) -> str:
        return re.sub(r"[\s，。！？；：、,.!?;:'\"“”‘’（）()【】\[\]{}<>《》]+", "", str(value or ""))

    normalized = [normalize(text) for text in texts]

    def grams(value: str) -> set[str]:
        return {value[index:index + 2] for index in range(max(0, len(value) - 1))}

    pairwise = []
    dup_count = 0
    severe_count = 0
    max_jaccard = 0.0
    max_pair = None
    for left in range(len(normalized)):
        for right in range(left + 1, len(normalized)):
            a, b = grams(normalized[left]), grams(normalized[right])
            score = len(a & b) / max(1, len(a | b))
            row = {"i": left, "j": right, "jaccard": score}
            pairwise.append(row)
            if score > SCORING_DUP_JACCARD:
                dup_count += 1
            if score > SCORING_SEVERE_JACCARD:
                severe_count += 1
            if score > max_jaccard:
                max_jaccard = score
                max_pair = [left, right]
    common_prefix_len = 0
    if normalized:
        common_prefix_len = len(normalized[0])
        for value in normalized[1:]:
            limit = min(common_prefix_len, len(value))
            index = 0
            while index < limit and normalized[0][index] == value[index]:
                index += 1
            common_prefix_len = index
    return {
        "pairwise": pairwise,
        "max_jaccard": max_jaccard,
        "max_pair": max_pair,
        "common_prefix_len": common_prefix_len,
        "dup_count": dup_count,
        "severe_count": severe_count,
    }


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

    def _strip_scoring_label(anchor):
        """Remove the label and its break from OOXML without flattening runs."""
        label = "（对应评分项"
        if not _paragraph_text(anchor).lstrip().startswith(label):
            return
        br = anchor.find(".//" + qn("w:br"))
        if br is None:
            for node in anchor.findall(".//" + qn("w:t")):
                node.text = ""
            return
        children = list(anchor)
        for child in children:
            if child.tag == qn("w:pPr"):
                continue
            if child.tag != qn("w:r"):
                anchor.remove(child)
                continue
            br = child.find(".//" + qn("w:br"))
            if br is None:
                anchor.remove(child)
                continue
            for node in list(child):
                if node is br:
                    child.remove(node)
                    break
                if node.tag == qn("w:rPr"):
                    continue
                child.remove(node)
            if not list(child.findall(".//" + qn("w:t"))):
                anchor.remove(child)
            break

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
    facts = {}
    for key, attr in (
        ("项目名称", "projectName"), ("招标范围", "bidContent"),
        ("计划工期", "deliveryDays"), ("质量标准", "quality"),
        ("投标有效期", "bidValidityDays"), ("投标保证金", "bidBondYuan"),
    ):
        value = getattr(brief, attr, None)
        if value not in (None, "", 0, 0.0):
            facts[key] = value
    drafted = {}
    if isinstance(citations, list):
        drafted = next((item.get("_scoring_drafts") for item in citations
                        if isinstance(item, dict) and isinstance(item.get("_scoring_drafts"), dict)), {}) or {}
    logger.info("scoring drafts extracted: count=%d citations_type=%s", len(drafted), type(citations).__name__)
    texts = []
    for title, anchor in anchors.items():
        selected = [point for point in points if point.target_section == title]
        if not selected:
            continue
        target = anchor
        following = anchor.getnext()
        if following is not None and following.tag == qn("w:p"):
            if _paragraph_text(following).strip().startswith("（对应评分项"):
                _strip_scoring_label(following)
                target = following
        generation_metadata: dict = {"section": title, "items": []}

        def draft_generator(point, facts_map):
            value = drafted.get(point.item, "")
            if isinstance(value, dict):
                return value
            return value

        rendered = section_blocks(
            selected,
            notes,
            facts=facts,
            generator=draft_generator,
            cited_text=lambda point: source_citation(point, blocks, uploaded, citations),
            metadata=generation_metadata,
        )
        scoring_meta = getattr(brief, "scoringMetadata", None)
        if isinstance(scoring_meta, dict):
            scoring_meta.setdefault("sections", []).append(generation_metadata)
        if not rendered:
            continue
        texts.extend((target, text) for text in rendered)
    return texts, notes

