from pathlib import Path
import re
p=Path('apps/api/services/tenders/performance.py'); s=p.read_text(encoding='utf-8')
s=re.sub(r'^_CHARGER_MARK = .*\n','',s,flags=re.M)
a=s.index('def is_charger_line('); b=s.index('\n\ndef dump_perf_meta',a); s=s[:a]+s[b:]
a=s.index('    charger = item.get("chargerRelated")'); b=s.index('    contact = ',a); s=s[:a]+s[b:]
s=s.replace('        chargerRelated=bool(charger),','        bidderName=str(item.get("bidderName") or item.get("seller") or "").strip(),')
a=s.index('    spec = ""\n    kw = re.search'); b=s.index('    return PerformanceLine(',a)
s=s[:a]+'''    spec_match = re.search(r"(?:规格型号|规格|型号|设备类型|服务范围)[：:]\\s*([^\\n。；;]{1,120})", blob)
    spec = spec_match.group(1).strip() if spec_match else ""
    seller_match = re.search(r"(?:乙方|卖方|承包人|供应商)(?:[（(]?全称[）)]?)?[：:]\\s*([^\\n]{2,80})", blob)
    bidder = seller_match.group(1).strip() if seller_match else ""
'''+s[b:]
s=s.replace('        chargerRelated=any(mark in blob for mark in _CHARGER_MARK),','        bidderName=bidder,').replace('否则已竣工优先，不再默认充电桩','否则按履约状态与合同金额排序')
a=s.index('_SCOPE_WORDS = ('); b=s.index('_SCOPE_JUNK = (',a); s=s[:a]+s[b:]
s=s.replace('{2,16}?)(?:项目|工程|合同|业绩)', '{2,48}?)(?:项目|工程|合同|业绩)')
a=s.index('def _clean_scope('); b=s.index('\n\ndef _type_tokens',a)
s=s[:a]+'''def _clean_scope(text: str) -> str:
    s = re.sub(r"\\s+", "", text or "").strip("的：:，,。；; ")
    if len(s) < 2 or any(j in s for j in _SCOPE_JUNK):
        return ""
    return s[:80]
'''+s[b:]
s=s.replace('找不到则空对象，不套用默认 20 万','找不到则空对象，不推断金额或数量')
s=s.replace('    keywords = [word for word in _SCOPE_WORDS if _word_in(blob, word)]','    keywords = []')
s=s.replace('''    if similar:
        for word in _SCOPE_WORDS:
            if _word_in(similar, word) and word not in keywords:
                keywords.append(word)
    if count <= 0:
        count = 3''','''    for scope in scopes:
        if scope not in keywords:
            keywords.append(scope)''')
s=s.replace('''    for word in _SCOPE_WORDS:
        if word in similar and word not in keywords:
            keywords.append(word)''','''    if similar and similar not in keywords:
        keywords.append(similar)''')
s=s.replace('''        if word in _CHARGER_SCOPE and is_charger_line(line):
            return True''','')
s=s.replace('''    if count <= 0 and 0 < right.minCount <= 20:
        count = right.minCount''','''    if count <= 0 and right.minCount > 0 and right.minCount == _count_from_blob("".join(_perf_windows(invitation))):
        count = right.minCount''')
s=s.replace('''    keywords = list(dict.fromkeys([*(left.keywords or []), *(right.keywords or [])]))[:8]''','''    evidence = "".join(_perf_windows(invitation))
    keywords = list(dict.fromkeys([*(left.keywords or []), *(w for w in (right.keywords or []) if _word_in(evidence, w))]))[:8]''')
s=s.replace('''        (right.similarScope or "").strip()
    )''','''        (right.similarScope or "").strip() if _word_in(evidence, right.similarScope or "") else ""
    )''')
p.write_text(s,encoding='utf-8')
p=Path('apps/api/services/tenders/classifier.py'); s=p.read_text(encoding='utf-8'); a=s.index('    for word in ("充电站", "充电桩", "供配电"):'); b=s.index('    brief.performanceRequirement = ',a)
s=s[:a]+'''    if scope and scope not in keywords:
        keywords.append(scope)
'''+s[b:]; p.write_text(s,encoding='utf-8')
p=Path('apps/api/services/tenders/assemble.py'); s=p.read_text(encoding='utf-8').replace('不再换成充电桩模块或自绘格子','不替换原版结构或自绘格子')
s=s.replace('''any(k in blob for k in ("数据采集", "合计")) and any(
        k in blob for k in ("TPM", "WMS", "MES", "QMS")
    )''','''"合计" in blob and sum(bool(re.search(r"数量|单价|金额|费用|报价", cell)) for cell in rows[0]) >= 2''',1)
s=s.replace('''    if any(p.upper() == "MOM" or p in {"MES", "WMS", "QMS", "TPM"} for p in plats):''','''    if len(plats) >= 2 and len({v for v in plats if v}) == 1 and len(plats[0]) <= 12:''')
s=s.replace('''any(k in blob for k in ("数据采集", "合计")) and any(
        k in blob for k in ("TPM", "WMS", "MES", "QMS")
    )''','''"合计" in blob and sum(bool(re.search(r"数量|单价|金额|费用|报价", cell)) for cell in headers) >= 2''',1)
p.write_text(s,encoding='utf-8')
p=Path('apps/api/services/tenders/document.py'); s=p.read_text(encoding='utf-8')
a=s.index('    blob = " ".join(',s.index('def infer_factory_role')); b=s.index('\n\ndef _factory_addressee',a)
s=s[:a]+'''    return "投标产品生产厂商"
'''+s[b:]
s=s.replace('本部分为技术标。评审看充电站如何建成：文字说明加本项目图纸。','本部分为技术标。按本次招标要求提供方案说明及所需图纸。').replace('("见模", "充电设备生产厂商", "投标产品生产厂商", "承担原厂责任", "联源热电")','("见模", "投标产品生产厂商", "承担原厂责任")').replace('《伟泰科技资质文件最终版.pdf》','本次投标人的资质 PDF')
p.write_text(s,encoding='utf-8')
p=Path('apps/api/services/tenders/demo.py'); s=p.read_text(encoding='utf-8').replace('name="充电桩设备及安装"','name=(brief.bidContent or "本次报价项目")'); p.write_text(s,encoding='utf-8')
print('Performance paths now use tender evidence, not sector tables')
