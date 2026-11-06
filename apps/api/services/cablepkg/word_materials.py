"""Word template reuse for cable bids, with source identity and placement audits."""
from __future__ import annotations

from copy import deepcopy
import hashlib
import io
import json
import tempfile
from zipfile import BadZipFile
from pathlib import Path
import re

from docx import Document
from docx.image.exceptions import UnrecognizedImageError
from lxml.etree import XMLSyntaxError
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.paragraph import Paragraph


def text(node):
    return "".join(t.text or "" for t in node.iter(qn("w:t"))).strip()


def title_key(value):
    value = re.sub(r"\s+", "", str(value or ""))
    value = re.sub(r"^(?:附表|表)\d+[_、.．：:]?", "", value)
    value = re.sub(r"^(?:第[一二三四五六七八九十百0-9]+[章节]|[一二三四五六七八九十]+[、.]|[（(][一二三四五六七八九十0-9]+[）)]|\d+(?:\.\d+)+[.、_．]?|\d+[.、_．])", "", value)
    value = re.sub(r"[（(](?:格式|完整版|补齐完整版|如有|择一版|清单式|填报版)[）)]", "", value)
    return value.strip("：:，,。；;")


def _toc(node):
    style = node.find("w:pPr/w:pStyle", node.nsmap)
    return bool(style is not None and re.match(r"TOC|目录", style.get(qn("w:val"), ""), re.I)
                or "PAGEREF" in node.xml or re.search(r"\t\s*\d+\s*$", Paragraph(node, None).text))


def _region(document, target, section_titles):
    candidates = [n for n in document.element.body if n.tag == qn("w:p")
                  and not _toc(n) and title_key(text(n)) == title_key(target)]
    if len(candidates) > 1:
        exact = [node for node in candidates if text(node) == target]
        if len(exact) == 1:
            candidates = exact
    if len(candidates) != 1:
        raise ValueError("目标章节缺失或不唯一：" + target)
    heading = candidates[0]
    members = []
    node = heading.getnext()
    known = {title_key(t) for t in section_titles if title_key(t)}
    while node is not None and node.tag != qn("w:sectPr"):
        if node.tag == qn("w:p") and not _toc(node) and title_key(text(node)) in known:
            break
        members.append(node)
        node = node.getnext()
    return heading, members


def _field(value):
    value = re.sub(r'\s+', '', str(value or ''))
    value = re.sub(r'^\d+[.、_．](?!\d)', '', value)
    value = re.sub(r"[（(].*?[）)]", "", value).strip("：:")
    return {"注册资金": "注册资本", "单位名称": "投标人名称", "公司名称": "投标人名称"}.get(value, value)


def _blank(value):
    if re.fullmatch(r'〔.*(?:按.*填|以.*为准|待填写|此处|须确认).*〕', value.strip()):
        return True
    return not re.sub(r"[\s〔〕【】\[\]（）()_＿—%％-]|待填写|待填|待补充", "", value)


def _cell_text(cell, value):
    paragraph = cell.paragraphs[0]
    run = paragraph.runs[0] if paragraph.runs else paragraph.add_run()
    run.text = value
    for other in paragraph.runs[1:]:
        other.text = ""
    for other in cell.paragraphs[1:]:
        other._p.getparent().remove(other._p)


def fill_tables(source, targets, mappings=(), *, cover=False):
    """Fill explicit coordinates or unique field/year matches in existing cells."""
    filled, pending = [], []
    if mappings:
        checked = []
        for mapping in mappings:
            if any(not isinstance(value, int) or value < 0 for value in mapping.values()):
                raise ValueError("表格坐标必须是非负整数")
            source_cell = source.tables[mapping["sourceTable"]].cell(mapping["sourceRow"], mapping["sourceColumn"])
            target_cell = targets[mapping["targetTable"]].cell(mapping["targetRow"], mapping["targetColumn"])
            checked.append((mapping, source_cell, target_cell))
        for mapping, source_cell, target_cell in checked:
            value = source_cell.text.strip()
            if value and not _blank(value) and _blank(target_cell.text):
                _cell_text(target_cell, value)
                filled.append(mapping)
        return filled, pending
    values = {}
    for table in source.tables:
        header = [re.sub(r"[〔〕\s]", "", c.text) for c in table.rows[0].cells]
        years = {i: re.search(r"20\d{2}年", v).group() for i, v in enumerate(header) if re.search(r"20\d{2}年", v)}
        for row in table.rows[1:]:
            cells = row.cells
            if years and len(cells) > 2:
                for i, year in years.items():
                    values.setdefault((_field(cells[1].text), year), set()).add(cells[i].text.strip())
            elif len(cells) == 2:
                role = _field(cells[0].text)
                value = cells[1].text.strip()
                values.setdefault((role, ""), set()).add(value)
                for match in re.finditer(r"(姓名|职务|技术职称|电话)[:：](.*?)(?=(?:姓名|职务|技术职称|电话)[:：]|$)", value):
                    values.setdefault((role + match.group(1), ""), set()).add(match.group(2).strip())
    for ti, table in enumerate(targets):
        header = [c.text for c in table.rows[0].cells]
        years = {i: re.search(r"20\d{2}年", v).group() for i, v in enumerate(header)
                 if re.search(r"20\d{2}年", v) and "示例" not in v}
        for ri, row in enumerate(table.rows):
            unique = []
            for ci, cell in enumerate(row.cells):
                if not unique or cell._tc is not unique[-1][1]._tc:
                    unique.append((ci, cell))
            candidates = []
            if years and ri:
                candidates = [(row.cells[ci], (_field(row.cells[1].text), year)) for ci, year in years.items()]
            elif not years:
                candidates = [(unique[i + 1][1], (_field(cell.text), ""))
                              for i, (_, cell) in enumerate(unique[:-1]) if cell.text.strip()]
            for cell, key in candidates:
                role = _field(row.cells[0].text)
                if (role + key[0], key[1]) in values:
                    key = (role + key[0], key[1])
                if cell.text.strip() in {'姓名', '职务', '技术职称', '电话'}:
                    continue
                options = {v for v in values.get(key, set()) if v and not _blank(v)}
                if key in values and not options and _blank(cell.text):
                    pending.append(key[0] + key[1])
                if len(options) == 1 and not _blank(cell.text) and cell.text.strip() not in options:
                    pending.append(key[0] + '（现值与资料候选值不一致）')
                if len(options) == 1 and _blank(cell.text):
                    value = options.pop()
                    _cell_text(cell, value)
                    filled.append({"table": ti, "row": ri, "field": key[0], "year": key[1], "value": value})
                    if re.search(r"〔|【|以.+为准|须确认", value):
                        pending.append(key[0])
    if cover:
        pattern = re.compile(r'(项目编号|项目名称|分标名称|分标编号|包名称|包号|项目单位|投标人名称|投标人)[：:]')
        seen = set()
        for ti, table in enumerate(targets):
            for ri, row in enumerate(table.rows):
                for cell in row.cells:
                    if cell._tc in seen:
                        continue
                    seen.add(cell._tc)
                    for paragraph in cell.paragraphs:
                        original = paragraph.text
                        matches = list(pattern.finditer(original))
                        for index in range(len(matches) - 1, -1, -1):
                            match = matches[index]
                            end = matches[index + 1].start() if index + 1 < len(matches) else len(original)
                            current = original[match.end():end]
                            key = match.group(1)
                            options = {v for v in values.get((key, ''), set()) if v and not _blank(v)}
                            if len(options) != 1 or not _blank(current):
                                continue
                            value = next(iter(options))
                            start, stop = match.end(), end
                            suffix = '　' if index + 1 < len(matches) else ''
                            position = 0
                            inserted = False
                            for run in paragraph.runs:
                                run_start, run_end = position, position + len(run.text)
                                position = run_end
                                if run_end <= start or run_start >= stop:
                                    continue
                                left = max(0, start - run_start)
                                right = min(len(run.text), stop - run_start)
                                run.text = run.text[:left] + (value + suffix if not inserted else '') + run.text[right:]
                                inserted = True
                            if not inserted:
                                paragraph.add_run(value + suffix)
                            filled.append({'table': ti, 'row': ri, 'field': key, 'year': '', 'value': value})
    return filled, pending



def _validate_guarantees(source):
    for table in source.tables:
        columns = set()
        for row in table.rows:
            for index, cell in enumerate(row.cells):
                if '保证值' in cell.text:
                    columns.add(index)
                elif index in columns and re.fullmatch(r'(?:完全)?响应|满足(?:要求)?|满足技术规范要求|按招标文件执行', cell.text.strip()):
                    raise ValueError('保证值列含空洞响应，须补充具体数值或状态')


def apply(document, bindings, sources, *, import_xml, section_titles):
    """Apply server-resolved source IDs only; never accept paths from bindings."""
    outcomes = []
    for binding in bindings:
        row = {"bindingId": binding["id"], "sourceDocumentId": binding["sourceDocumentId"],
               "targetId": binding["targetId"], "targetTitle": binding["targetTitle"], "mode": binding["mode"],
               "reviewRequired": True, "evidenceLevel": "L4", "contentScope": binding.get("contentScope", "section")}
        try:
            source_path = Path(sources[binding["sourceDocumentId"]])
            raw = source_path.read_bytes()
            if hashlib.sha256(raw).hexdigest() != binding["sourceHash"]:
                raise ValueError("资料版本已变化，请重新确认绑定")
            if source_path.suffix.lower() == '.doc':
                from api.services.word_convert import convert_doc_to_docx, WordConversionError
                try:
                    with tempfile.TemporaryDirectory(prefix='cable-word-') as temporary:
                        converted = Path(temporary) / 'source.docx'
                        convert_doc_to_docx(source_path, converted)
                        source = Document(converted)
                except WordConversionError as exc:
                    raise ValueError(str(exc)) from exc
            else:
                source = Document(io.BytesIO(raw))
            _validate_guarantees(source)
            if binding["mode"] == "reference":
                row.update(status="reference", reason="检查规则或填报说明，供填报核对")
                outcomes.append(row)
                continue
            marker_name = "word_binding_" + hashlib.sha256(json.dumps(binding, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:24]
            if any(mark.get(qn("w:name")) == marker_name for mark in document.element.body.iter(qn("w:bookmarkStart"))):
                row.update(status="filled" if binding["mode"] == "table" else "copied", alreadyApplied=True)
                outcomes.append(row)
                continue
            heading, members = _region(document, binding["targetTitle"], section_titles)
            if binding["mode"] == "table":
                targets = [Table(node, document._body) for node in members if node.tag == qn("w:tbl")]
                filled, pending = fill_tables(source, targets, binding.get("tableMappings", []), cover=binding.get("contentScope") == "cover")
                row.update(status="filled" if filled else "needs_review", fields=filled, pendingFields=pending)
                if not targets:
                    row["reason"] = "目标章节没有可填表格，须补充原版表格"
                elif not filled:
                    row["reason"] = "来源没有可用值、原表已有值或尚未建立字段映射，须核对"
                if filled:
                    for node in members:
                        if node.tag == qn("w:p") and "所需材料" in text(node):
                            node.getparent().remove(node)
            elif binding["mode"] == "reference":
                row.update(status="reference", reason="检查规则或填报说明，供填报核对")
            else:
                copied = []
                numbering_map = {}
                for index, original in enumerate(source.element.body):
                    value = text(original)
                    if original.tag == qn("w:p") and _toc(original):
                        continue
                    if original.tag == qn("w:sectPr") or value.startswith(("说明：", "提示：", "说明:", "提示:")):
                        continue
                    if index == 0 and original.tag == qn("w:p") and section_key(value) == section_key(binding.get("sourceTitle") or binding["targetTitle"]):
                        continue
                    node = deepcopy(original)
                    import_xml(document, source, node, numbering_map=numbering_map,
                               style_prefix="material_" + binding["sourceHash"][:12] + "_")
                    copied.append(node)
                if not copied:
                    raise ValueError("资料正文为空")
                retained_scans = [deepcopy(node) for node in members
                                  if list(node.iter(qn('w:drawing')))
                                  and any(mark.get(qn('w:name'), '').startswith('result_')
                                          and mark.get(qn('w:name'), '').endswith('_img')
                                          for mark in node.iter(qn('w:bookmarkStart')))
                                  and node.find('w:pPr/w:sectPr', node.nsmap) is None]
                # Replacing the company's name-change statement must not erase
                # the separate conditional requirements for authority-issued proofs.
                proof_tokens = {'result_' + hashlib.sha256(key.encode()).hexdigest()[:20] + '_box'
                                for key in ('cable_name_change_registry', 'cable_name_change_qualification')}
                retained_proofs = [deepcopy(node) for node in members
                                   if node.tag == qn('w:p')
                                   and node.find('w:pPr/w:pBdr', node.nsmap) is not None
                                   and any(mark.get(qn('w:name'), '') in proof_tokens
                                           for mark in node.iter(qn('w:bookmarkStart')))]
                for node in members:
                    if node.find("w:pPr/w:sectPr", node.nsmap) is None:
                        node.getparent().remove(node)
                cursor = heading
                for node in [*copied, *retained_scans, *retained_proofs]:
                    cursor.addnext(node)
                    cursor = node
                row.update(status="copied", blocks=len(copied), retainedScanBlocks=len(retained_scans), reviewRequired=True)
            if row.get("status") in {"copied", "filled"}:
                from docx.oxml import OxmlElement
                bookmark_id = str(max((int(mark.get(qn("w:id"), "0")) for mark in document.element.body.iter(qn("w:bookmarkStart"))), default=0) + 1)
                start = OxmlElement("w:bookmarkStart")
                start.set(qn("w:name"), marker_name)
                start.set(qn("w:id"), bookmark_id)
                end = OxmlElement("w:bookmarkEnd")
                end.set(qn("w:id"), bookmark_id)
                heading.append(start)
                heading.append(end)
        except (KeyError, ValueError, OSError, IndexError, BadZipFile, UnrecognizedImageError, XMLSyntaxError) as exc:
            row.update(status="needs_review", reason=str(exc))
        outcomes.append(row)
    return outcomes


_TITLE_ALIASES = (
    (r"^投标函（待填项与联系信息）$", "投标函"),
    (r"^(?:XX年|20\d{2}年)?财务信息审核结果凭证单(?:说明)?$", "财务信息审核结果凭证单"),
    (r"^符合.*资格要求的证明文件(?:清单)?$", "资格要求证明文件"),
    (r"^投标保证金(?:情况说明（含明细表）|（含年度保证金）（不适用）)?$", "投标保证金说明"),
    (r"^技术服务(?:方案|[，,、]|包括|（|\().*", "技术服务"),
    (r"^财务信息.*股权结构.*", "财务信息及股权结构"),
    (r"^投标人与.*系统人员关系说明$", "投标人与系统人员关系说明"),
    (r"^法定代表人.*授权委托书$", "法定代表人授权委托书"),
    (r"^.*企业名称变更.*说明.*$", "企业名称变更说明"),
)


def section_key(value):
    value = title_key(value)
    announcement = re.search(r'招标公告(\d+(?:\.\d+)+).*商务支撑', value)
    if announcement:
        return '商务支撑材料_公告' + announcement.group(1)
    for pattern, canonical in _TITLE_ALIASES:
        if re.fullmatch(pattern, value):
            return canonical
    return value


def usage(title):
    if re.search(r"填写规则|自查|风险自检|待填项|清单|截图说明|不适用说明|情况说明（含明细表）|资格预审结果通知书|财务信息审核结果凭证单说明", title):
        return "reference"
    if re.search(r"基本情况表|财务信息.*股权结构", title):
        return "table"
    return "chapter"


def bind(outline, entries, previous=()):
    """Bind unique section titles to immutable document IDs and content hashes."""
    bindings, review = [], []
    old = {b["targetId"]: b for b in previous}
    seen_targets = set()
    # Exact repeated format headings belong to the most specific later entry.
    targets = {}
    for item in outline:
        if item.get("skipped"):
            continue
        key = section_key(item.get("rawTitle") or item["title"])
        if key == "承诺函" and re.search(r"供货前.*型式试验报告", item.get("context", "")):
            key = "型式试验报告承诺函"
        targets[key] = item
    for key, item in targets.items():
        if not key:
            continue
        target_id = item["id"]
        saved = old.get(target_id)
        if saved is None and any(section_key(binding['targetTitle']) == section_key(item.get('rawTitle') or item['title']) for binding in old.values()):
            review.append({'targetId': target_id, 'status': 'needs_review', 'reason': '目标章节 ID 已变化，请重新确认绑定'})
            continue
        if saved is None and item.get("draftStatus") in {"manual", "edited"}:
            continue
        if saved and section_key(saved["targetTitle"]) != section_key(item.get("rawTitle") or item["title"]):
            review.append({"targetId": target_id, "status": "needs_review", "reason": "目标章节名称已变化，请重新确认绑定"})
            continue
        if saved:
            matches = [e for e in entries if e["documentId"] == saved["sourceDocumentId"]
                       and e["hash"] == saved["sourceHash"] and e["itemId"] == saved["sourceItemId"] and e.get("eligible")]
            if not matches:
                review.append({"targetId": target_id, "status": "needs_review", "reason": "已绑定资料被删除、版本变化或不适用于本项目"})
                continue
        else:
            matches = [e for e in entries if e.get("eligible") and section_key(e["title"]) == key]
            if key in {'技术特性参数表', '货物组件材料配置表'}:
                matches = [e for e in entries if e.get('eligible') and title_key(e['title']) == '参数表与配置表封面填写规则']
        if len(matches) != 1:
            if len(matches) > 1:
                review.append({"targetId": target_id, "status": "needs_review", "reason": "同一章节有多份候选资料，请选择版本",
                               "candidateDocumentIds": [entry["documentId"] for entry in matches]})
            continue
        entry = matches[0]
        mode = saved["mode"] if saved else usage(entry["title"])
        if not saved and key in {'技术特性参数表', '货物组件材料配置表'}:
            mode = 'table'
        target_title = item.get("rawTitle") or item["title"]
        binding = dict(saved) if saved else dict(
            id="word_" + hashlib.sha256((target_id + entry["documentId"] + entry["hash"]).encode()).hexdigest()[:24],
            targetId=target_id, targetTitle=target_title, sourceItemId=entry["itemId"],
            sourceDocumentId=entry["documentId"], sourceTitle=entry["title"], sourceHash=entry["hash"], mode=mode,
            reviewRequired=True, volume=item.get("volume", "full"))
        if key in {"技术特性参数表", "货物组件材料配置表"} and "封面填写规则" in entry["title"]:
            binding["contentScope"] = "cover"
        bindings.append(binding)
        seen_targets.add(target_id)
    for target_id, saved in old.items():
        if target_id not in seen_targets and target_id not in {r["targetId"] for r in review}:
            review.append({"targetId": target_id, "status": "needs_review", "reason": "原目标章节已变化，请重新确认绑定"})
    used = {b["sourceDocumentId"] for b in bindings}
    for entry in entries:
        if entry["documentId"] not in used:
            review.append({"sourceDocumentId": entry["documentId"], "title": entry["title"],
                           "status": "reference" if usage(entry["title"]) == "reference" else "unmatched",
                           "reason": "填报参考资料" if usage(entry["title"]) == "reference" else "没有唯一对应章节或尚未确认本项目适用性"})
    return bindings, review


_NON_MATERIAL_TITLES = {
    "项目名称", "招标人名称", "投标人名称", "招标编号", "项目编号", "分标名称", "分标编号", "包名称", "包号",
    "联系人", "联系方式", "电话", "地址", "邮政编码", "保险险种", "被保险人", "保险金额", "绝对免赔额",
    "项目理解", "技术路线", "实施步骤", "功能参数响应", "功能参数响应解决办法", "项目概况", "服务范围与目标",
}


def material_title_valid(value):
    from .material_binding import is_field_requirement
    return title_key(value) not in _NON_MATERIAL_TITLES and not is_field_requirement(title_key(value))


def prune_placeholder_boxes(document, outcomes=(), bindings=()):
    resolved = _resolved_titles(outcomes, bindings)
    removed = []
    for node in list(document.element.body):
        if node.tag != qn("w:p") or node.find("w:pPr/w:pBdr", node.nsmap) is None:
            continue
        match = re.fullmatch(r"对应\s*(.*?)；所需材料：\s*(.*)", text(node))
        if match and title_key(match.group(1)) == title_key(match.group(2)) and (not material_title_valid(match.group(1)) or _placement_key(match.group(1)) in resolved):
            removed.append(match.group(1))
            node.getparent().remove(node)
    return removed



def _placement_key(value):
    # A company statement cannot stand in for the issuing authority's proof.
    if '变更证明' in value or '证明材料' in value:
        return title_key(value)
    return section_key(value)


def _resolved_titles(outcomes, bindings):
    by_id = {binding["id"]: binding for binding in bindings}
    resolved = set()
    for row in outcomes:
        binding = by_id.get(row["bindingId"], {})
        if row.get('contentScope') == 'cover' or binding.get('contentScope') == 'cover':
            continue
        if row["status"] in {"copied", "filled"}:
            source_title = binding.get('sourceTitle') or row['targetTitle']
            resolved.add(_placement_key(source_title))
    if {'技术服务', '售后服务保障及承诺'} <= resolved:
        resolved.add('技术服务和质保期服务计划')
    return resolved


def reconcile_match(report, outcomes, bindings):
    """Separate Word placements from missing original supporting evidence."""
    resolved = _resolved_titles(outcomes, bindings)
    result = dict(report)
    for group in ["missingFiles", "createdItems"]:
        result[group] = [row for row in report.get(group, []) if _placement_key(row["title"]) not in resolved]
    result["wordMaterials"] = outcomes
    return result
