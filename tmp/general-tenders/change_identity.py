from pathlib import Path
import ast

root=Path('apps/api/services/tenders')
p=root/'schema.py'; s=p.read_text(encoding='utf-8')
tree=ast.parse(s); edits=[]
for cls in tree.body:
 if isinstance(cls,ast.ClassDef) and cls.name=='BidBrief':
  for n in cls.body:
   if isinstance(n,ast.AnnAssign) and isinstance(n.target,ast.Name) and n.target.id in {
    'bidderName','bidderNature','bidderAddress','bidderPhone','bidderEmail','foundedDate','businessTerm',
    'legalPersonName','legalPersonGender','legalPersonAge','legalPersonTitle','legalPersonIdNo','trafficFeeNote'}:
    edits.append((n.value,'Field(default="")'))
lines=s.encode().splitlines(keepends=True); offsets=[0]
for line in lines: offsets.append(offsets[-1]+len(line))
data=s.encode()
for n,v in sorted(edits,key=lambda a:a[0].lineno,reverse=True):
 data=data[:offsets[n.lineno-1]+n.col_offset]+v.encode()+data[offsets[n.end_lineno-1]+n.end_col_offset:]
s=data.decode().replace('没有抽出则为空，不套用充电桩默认 20 万','没有抽出则为空，不套用默认数量或金额')
s=s.replace('    chargerRelated: bool = Field(default=False)', '    bidderName: str = Field(default="")\n    chargerRelated: bool = Field(default=False)')
p.write_text(s,encoding='utf-8')
p=root/'commitment.py'; s=p.read_text(encoding='utf-8').replace('(brief.bidderName or "").strip() or "河南伟泰光电科技有限公司"','(brief.bidderName or "").strip()'); p.write_text(s,encoding='utf-8')
p=root/'placeholders.py'; s=p.read_text(encoding='utf-8').replace('签约主体必须是河南伟泰光电科技有限公司。上传后识别项目/买方/合同额；生成本标时按邀请书业绩门槛（行业、MES/MOM 等）筛选，不再默认充电桩。','签约主体须与本次投标人一致。上传后识别项目、签约主体和合同金额，按本次招标条款筛选。').replace('【待补】请粘贴河南伟泰光电科技有限公司作为签约主体的合同及发票。','【待补】请粘贴本次投标人作为签约主体的合同及招标要求的证明材料。').replace('所投产品检测报告 / 3C / 对应功率桩型证明','所投产品检测报告 / 认证 / 参数证明').replace('功率与招标要求一致；正偏离须另附厂家盖章说明。','产品型号、参数和认证应与招标要求一致。'); p.write_text(s,encoding='utf-8')
p=root/'history.py'; s=p.read_text(encoding='utf-8')
s=s.replace('BIDDER_LOCK = "河南伟泰光电科技有限公司"\nFOREIGN_BIDDER_MARKS = ("郑州容新", "容新新能源")','from api.services.tenders.company_identity import same_company, foreign_bidder')
a=s.index('def contains_foreign_bidder('); b=s.index('\n\ndef sanitize_history_brief',a)
s=s[:a]+'''def contains_foreign_bidder(text: str, bidder_name: str = "") -> bool:
    return foreign_bidder(text, bidder_name)
'''+s[b:]
s=s.replace('raw: BidBrief | dict | None) -> HistoryPlaybook | None:', 'raw: BidBrief | dict | None, *, bidder_name: str = "") -> HistoryPlaybook | None:')
s=s.replace('    if bidder and BIDDER_LOCK not in bidder:', '    if bidder_name and not same_company(bidder, bidder_name):')
s=s.replace('contains_foreign_bidder(bidder) or contains_foreign_bidder(brief.factoryRole or "")','contains_foreign_bidder(brief.factoryRole or "", bidder)')
s=s.replace('contains_foreign_bidder(role):','contains_foreign_bidder(role, bidder):')
s=s.replace('async def latest_company_profile(db: AsyncSession, *, limit: int = 8)', 'async def latest_company_profile(db: AsyncSession, *, bidder_name: str = "", limit: int = 8)')
s=s.replace('    stmt = (\n        select(TenderRecord)', '    if not bidder_name.strip():\n        return {}\n    stmt = (\n        select(TenderRecord)',1)
s=s.replace('if name and BIDDER_LOCK not in name:', 'if not same_company(name, bidder_name):')
s=s.replace('contains_foreign_bidder(name) or contains_foreign_bidder(str(raw.get("legalPersonName") or ""))','contains_foreign_bidder(str(raw.get("legalPersonName") or ""), bidder_name)')
s=s.replace('profile["bidderName"] = BIDDER_LOCK','profile["bidderName"] = name')
s=s.replace('async def latest_agent_profile(db: AsyncSession, *, limit: int = 8)', 'async def latest_agent_profile(db: AsyncSession, *, bidder_name: str = "", limit: int = 8)')
marker='    """最近一次启用过授权委托的姓名/证号，打开开关时回填。"""'
s=s.replace(marker,marker+'\n    if not bidder_name.strip():\n        return {}')
start=s.index('async def latest_agent_profile'); a=s[:start]; b=s[start:]
b=b.replace('        name = str(raw.get("agentName")', '        if not same_company(raw.get("bidderName"), bidder_name):\n            continue\n        name = str(raw.get("agentName")',1); s=a+b
s=s.replace('async def load_history_playbooks(db: AsyncSession, *, limit: int = _HISTORY_LIMIT)', 'async def load_history_playbooks(db: AsyncSession, *, bidder_name: str = "", limit: int = _HISTORY_LIMIT)')
start=s.index('async def load_history_playbooks'); a=s[:start]; b=s[start:]
b=b.replace('    stmt = (','    if not bidder_name.strip():\n        return []\n    stmt = (',1).replace('book = sanitize_history_brief(row.brief_json if isinstance(row.brief_json, dict) else None)','book = sanitize_history_brief(row.brief_json if isinstance(row.brief_json, dict) else None, bidder_name=bidder_name)'); s=a+b
p.write_text(s,encoding='utf-8')
p=root/'extract.py'; s=p.read_text(encoding='utf-8').replace('    BIDDER_LOCK,\n','').replace('_BIDDER_LOCK = BIDDER_LOCK\n','')
a=s.index('    data["bidderName"] = _BIDDER_LOCK if'); b=s.index('    from api.services.tenders.outline import _clean_pm_name',a)
s=s[:a]+'    data["bidderName"] = (base.bidderName or "").strip()\n'+s[b:]
s=s.replace('    sources.append(default_brief().model_dump())','''    from api.services.tenders.company_identity import same_company
    selected = (brief.bidderName or "").strip()
    if not selected:
        selected = next((str(p.get("bidderName") or "").strip() for p in sources if p.get("bidderName")), "")
    sources = [p for p in sources if same_company(p.get("bidderName"), selected)]''')
s=s.replace('    data["bidderName"] = _BIDDER_LOCK\n','    data["bidderName"] = selected\n')
s=s.replace('if contains_foreign_bidder(invitation):','if contains_foreign_bidder(invitation, (current.bidderName if current else "")):')
s=s.replace('playbooks = await load_history_playbooks(db)','playbooks = await load_history_playbooks(db, bidder_name=(current.bidderName if current else ""))')
s=s.replace('_user_prompt(invitation, kb_hits, catalog_rows, playbooks)','_user_prompt(invitation, kb_hits, catalog_rows, playbooks, bidder_name=(current.bidderName if current else ""))')
s=s.replace('company_profile = await latest_company_profile(db)','company_profile = await latest_company_profile(db, bidder_name=brief.bidderName)')
s=s.replace('agent_hint = await latest_agent_profile(db)','agent_hint = await latest_agent_profile(db, bidder_name=brief.bidderName)')
s=s.replace('    playbooks: list | None = None,\n) -> str:', '    playbooks: list | None = None,\n    *, bidder_name: str = "",\n) -> str:')
s=s.replace('f"投标人（不得修改）：{_BIDDER_LOCK}"','f"投标人（以本次 brief 的 bidderName 为准，不得从招标范例替换）：{bidder_name or \'【待补充】\'}"')
p.write_text(s,encoding='utf-8')
for filename in ('extract_system.md','qa_system.md','performance_system.md'):
 p=root/'prompts'/filename; s=p.read_text(encoding='utf-8')
 s=s.replace('投标人永远是河南伟泰光电科技有限公司','投标人以本次 brief 的 bidderName 为准，缺失时留空并提示').replace('投标人是河南伟泰光电科技有限公司','投标人（以本次 brief 的 bidderName 为准）').replace('伟泰资料','本次投标人的资料').replace('伟泰合同业绩','本次投标人的合同业绩')
 if filename=='performance_system.md':
  s='你从合同或发票扫描识别文本中抽取一条投标业绩。只填文本里能确认的字段，禁止编造。投标人以本次 brief 的 bidderName 为准；此处抽取文本中的签约主体，不擅自代入其他公司。只输出一个 JSON 对象。字段：bidderName（卖方/乙方/承包人全称）, projectName（工程/合同名称）, spec（文本中的产品型号或服务范围）, client（买方/甲方）, contact（甲方联系人，没有则空字符串）, amountYuan（合同额，单位元，数字）, summary（文本中的项目内容及履约情况）, ongoing（true=尚未完成，false=已完成）, note（补充）。找不到的字段留空，不预设行业、设备或金额。'
 p.write_text(s,encoding='utf-8',newline='')
print('Company identity migration applied')
