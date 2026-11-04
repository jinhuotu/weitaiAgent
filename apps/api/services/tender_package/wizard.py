"""Read-only issue projections. All answers use the existing versioned action API.

Collectors receive an existing PackageStore.view snapshot; they must never write,
match files, or manufacture business values. Register new collectors, not UI rules.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import PureWindowsPath
import json
import re
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict

ISSUE_TYPES = ('material_conflict', 'role_ambiguity', 'missing_field', 'price_missing',
               'business_missing', 'file_problem', 'rule_uncovered')
COLLECTORS = {}


class Evidence(BaseModel):
    label: str
    locator: str
    quote: str


class IssueAction(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    kind: Literal['single_choice', 'multi_choice', 'text', 'number', 'file', 'confirm']
    options: list[dict] = Field(default_factory=list)
    schema_: dict = Field(alias='schema')
    default: object = None


class Issue(BaseModel):
    id: str
    type: str  # Open extension point; built-in names are listed in ISSUE_TYPES.
    severity: Literal['blocking', 'warning', 'info']
    title: str
    description: str
    evidence: list[Evidence] = Field(default_factory=list)
    action: IssueAction
    target_action: dict | None = None
    status: Literal['open', 'resolved', 'deferred'] = 'open'
    unavailable_reason: str | None = None


def register(name):
    def add(fn):
        if name in COLLECTORS:
            raise ValueError('问题收集器名称重复')
        COLLECTORS[name] = fn
        return fn
    return add


def logical(value):
    text = str(value or '').replace('\\', '/')
    if PureWindowsPath(text).drive or text.startswith('/') or '..' in text.split('/'):
        return text.rsplit('/', 1)[-1]
    return text


def human(value):
    text = str(value or '')
    return re.sub(r'(?:[A-Za-z]:[\\/]|(?<!\w)/(?:home|storage|tmp|Users|var)/)[^\s\"<>]+',
                  '[来源文件]', text)


def evidence(state, sources):
    docs = {d['document_id']: d for d in (state.get('ssot') or {}).get('documents', [])}
    result = []
    for source in sources:
        doc = docs.get(source.get('document_id'), {})
        path = logical(source.get('logical_path') or source.get('source_file') or doc.get('logical_path'))
        location = human(source.get('locator') or '')
        location = re.sub(r'table:(\d+)/row:(\d+)',r'表 \1 · 第 \2 行',location)
        location = re.sub(r'paragraph:(\d+)',r'第 \1 段',location)
        if source.get('row'):
            location = f"表 {source.get('table_index', 0)} · 第 {source['row']} 行"
        result.append(dict(label=human(source.get('label') or '原始依据'),
                           locator=path + ((' · ' + location) if location else ''),
                           quote=human(source.get('quote', source.get('text', source.get('raw_value', source.get('value', '')))))))
    return result


def text(title, **kwargs):
    return dict(dict(type='string', title=title, minLength=1, pattern=r'\S'), **kwargs)


def choice(title, options, **kwargs):
    return dict(type='string', title=title, enum=[o['value'] for o in options],
                **{'x-options': options}, **kwargs)


def answer(name):
    return {'$answer': name}


def target(operation, arguments=None, review=None):
    args = arguments or {}
    if review:
        args = dict(operation=review, arguments=args)
    return dict(operation=operation, arguments=args)


def issue(kind, key, title, description, properties=None, required=None, *, action=None,
          status='open', severity='warning', sources=(), state=None, schema_extra=None,
          unavailable=None, form_kind='text', identity=None):
    props = deepcopy(properties or {})
    if action and action['operation'] not in ('library', 'unpack'):
        props['basis'] = text('确认依据')
    schema = dict(type='object', properties=props, required=required or list(props), additionalProperties=False)
    schema.update(schema_extra or {})
    namespace=identity or kind
    key_digest = sha256(json.dumps([namespace, key], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:24]
    return Issue(id=f'{namespace}:{key_digest}', type=kind, severity=severity,
                 title=human(title), description=human(description), evidence=evidence(state or {}, sources),
                 action=IssueAction(kind=form_kind, schema=schema), target_action=action,
                 status=status, unavailable_reason=unavailable)


@register('roles')
def roles(s):
    from .recognition import ROLE_LABELS
    if not s.get('classified') or s.get('ssot'):
        return []
    result = []
    for f in s['classified']['files']:
        if f.get('role') not in ('other', 'pending') and not f.get('manual_override'):
            continue
        if f.get('file_type') in ('.zip', '.rar', '.7z'):
            continue  # Containers are provenance, not uncertain body documents.
        opts = [dict(value=k, label=v) for k, v in ROLE_LABELS.items() if k != 'pending']
        result.append(issue('role_ambiguity', f['storage_path'], '请确认文件用途',
            logical(f['logical_path']), dict(role=choice('文件用途', opts)),
            action=target('role', dict(storage_path=f['storage_path'], role=answer('role'), basis=answer('basis'))),
            status='resolved' if f.get('manual_override') else 'open',
            sources=[dict(logical_path=f['logical_path'], quote='；'.join(f.get('role_evidence', [])))]))
    return result


@register('materials')
def materials(s):
    if not s.get('classified'):
        return []
    from .recognition import describe
    from .composed import load_rules
    rows = (s.get('recognition') or describe(s['classified']))['entries']
    values = sorted(set(load_rules()['conductor_values'].values()))
    result = []
    for m in rows:
        if not m['needs_confirmation'] and not m['historical_conflict'] and m.get('source_type')!='manual':
            continue
        sources = []
        for record in m['sources']:
            sources.append(dict(label='来源名称（' + record.get('source_type', '') + '）',
                source_file=record.get('source_file'), quote=record.get('name') or record.get('basis', '')))
            for field, entries in record.get('parameters', {}).items():
                sources.extend(dict(e, label=e.get('label', field), source_file=record.get('source_file')) for e in entries)
            for field, value in record.get('confirmed_parameters', {}).items():
                label={'model':'型号','conductor':'导体材质'}.get(field,'参数')
                sources.append(dict(label='既有人工确认 · ' + label, quote=str(value) + '；' + record.get('basis', ''),
                                    source_file=record.get('source_file')))
        frozen = bool(s.get('ssot')) and m['needs_confirmation']
        properties=dict(name=text('最终完整物料名称'))
        # Only expose domain parameters already supplied by the existing composer.
        # Other materials use the existing generic name-confirmation action.
        if m.get('composition_history'):
            properties.update(model=text('最终型号'),conductor=choice('最终导体材质',[dict(value=v,label=v) for v in values]))
        arguments=dict(solidified_id=m['solidified_id'],basis=answer('basis'),**{k:answer(k) for k in properties})
        result.append(issue('material_conflict', m['solidified_id'], '物料名称或参数需要裁决 · ' + m['solidified_id'],
            '原始识别与人工来源并列保留，请核对后填写最终名称与参数。系统不会替你选择。',
            properties,action=None if frozen else target('material',arguments),
            status='open' if m['needs_confirmation'] else 'resolved', severity='blocking', sources=sources,
            unavailable='物料映射已冻结，请到高级模式新建任务核对来源，不覆盖既有事实。' if frozen else None))
    return result


@register('packages')
def packages(s):
    if not s.get('ssot'):
        return []
    model = s['ssot']; session = s['inputs']
    lots = [dict(value=l['key'], label=' '.join(str(f.get('value') or '') for f in l['fields'].values()) or '未命名分标') for l in model['lots']]
    ids = [dict(value=m['solidified_id'], label=m['solidified_id'] + ' · ' + m['material_mapping']['material_name']) for m in model['materials']]
    item = dict(type='object', title='一个实际标包', required=['key', 'package_code', 'lot_key', 'material_ids'], properties=dict(
        key=text('输出目录代号（字母、数字、下划线）', **{'pattern': r'^[A-Za-z0-9_-]+$'}),
        package_code=text('实际包号'), lot_key=choice('所属分标', lots),
        material_ids=dict(type='array', title='纳入的物料', minItems=1, uniqueItems=True,
                          items=dict(type='string', enum=[o['value'] for o in ids]), **{'x-kind': 'multi_choice', 'x-options': ids})))
    active = bool(session.get('package_history'))
    return [issue('missing_field', 'package-allocation', '确认实际标包与物料范围',
        '请按已确认的采购范围分配全部物料，每项只能归属一个标包；不根据文件夹名称推断。',
        dict(packages=dict(type='array', title='标包分配', minItems=1, items=item)),
        action=target('review', dict(packages=answer('packages'), basis=answer('basis')), 'packages'),
        status='resolved' if active else 'open', severity='blocking',
        sources=[e for l in model['lots'] for e in l.get('row_sources', [])], state=s,
        unavailable=None if lots else '没有可靠的分标候选，请先到高级模式核对招标来源。')]


@register('prices')
def prices(s):
    if not s.get('review'):
        return []
    review = s['review']; confirmed = review.get('packages') or []
    valid_sources={q['sha256'] for q in review.get('approved_quotes',{}).get('sources',[])}
    by_package={p['key']:p for p in confirmed}
    def usable(p):
        ref=p.get('approved_quote_evidence')
        return not ref or (ref['sha256'] in valid_sources and p.get('solidified_id') in by_package.get(p['package_key'],{}).get('material_ids',[]))
    fills = {(p['package_key'], p['document_id'], p['table'], p['row'], p['column']) for p in review.get('price_history', []) if usable(p)}
    rejected = {(p.get('source_document'), r['table'], r['row'], r['column']): r
                for p in (s.get('result') or {}).get('price', []) for r in p.get('proof', {}).get('rejected', [])}
    options = [dict(value=p['key'], label=p['package_code']) for p in confirmed]
    result = []
    for t in review.get('price_targets', []):
        key = tuple(t[k] for k in ('document_id', 'table', 'row', 'column'))
        filled = bool(confirmed) and all((p['key'], *key) in fills for p in confirmed)
        protected = rejected.get(key)
        result.append(issue('price_missing', key, '填写实际报价 · ' + human(t['title']).split('\n')[0],
            '仅填写已批准的实际价格；不同报价类型须自行核对计价单位及金额关系。'
            + ((' 原位置未写入：' + protected['reason']) if protected else ''),
            dict(package_key=choice('所属已确认标包', options), amount=dict(type='number', title='实际金额', minimum=0),
                 currency=text('币种'), unit=text('计价单位')),
            action=target('review', dict(**{k:t[k] for k in ('document_id','table','row','column')},
                 package_key=answer('package_key'), amount=answer('amount'), currency=answer('currency'),
                 unit=answer('unit'), basis=answer('basis')), 'price'),
            status='deferred' if protected else ('resolved' if filled else 'open'),
            sources=[dict(t['source'], logical_path=t['source_document']['logical_path'])],
            unavailable=('合并位置受保护，须在高级模式人工核对原表；重复提交不会解除保护。' if protected
                         else (None if confirmed else '请先确认标包与物料范围。'))))
    return result


@register('parameters')
def parameters(s):
    catalog=(s.get('review') or {}).get('catalog', {})
    pending=catalog.get('pending', [])
    rows=[*pending,*catalog.get('automatic', [])]
    open_keys={(r['solidified_id'],r['target']) for r in pending}
    def sources(r):
        result=[dict(r['source'], logical_path=r['source_document']['logical_path'])]
        if r.get('guarantee'):
            result.append(dict(label='已有保证值（来源记录）',quote=r['guarantee'],logical_path=r['source_document']['logical_path']))
        for c in r.get('conflict',{}).get('candidates',[]):
            result.append(dict(label='产品资料候选值',quote=c.get('value',''),logical_path=c.get('path'),locator=c.get('locator')))
        for c in r.get('conflict',{}).get('confirmed_evidence',[]):
            result.append(dict(label='既有人工确认',quote=str(c.get('confirmed_value',''))+'；'+c.get('basis',''),
                               logical_path=r['source_document']['logical_path'],**{k:c[k] for k in ('row','table_index') if k in c}))
        return result
    return [issue('material_conflict' if r.get('conflict') else 'missing_field', [r['solidified_id'], r['target']],
        '核对产品参数 · ' + (r['label'] or '原表参数'),
        r['solidified_id'] + ' · ' + r.get('review_reason','已有依据的参数结论已记录') + '。请提供有依据的保证值及响应结论。',
        dict(guarantee=text('我方实际保证值'), status=choice('核对结论', [dict(value=v,label=v) for v in ('响应','偏离')])),
        action=target('review', dict(basis=answer('basis'), decisions=[dict(solidified_id=r['solidified_id'],
            target=r['target'], guarantee=answer('guarantee'), status=answer('status'))]), 'review_rows'),
        severity='blocking' if r.get('conflict') else 'warning',
        status='open' if (r['solidified_id'],r['target']) in open_keys else 'resolved',
        sources=sources(r), state=s,identity='parameter') for r in rows]


@register('business')
def business(s):
    if not s.get('ssot'):
        return []
    if not s.get('library'):
        return [issue('business_missing', 'load-library', '读取真实资料候选',
            '使用当前账号权限查询资料库，供后续选择真实文件；不会自动确认资料存在或缺失。',
            dict(confirmed=dict(type='boolean', title='读取资料库候选', const=True)),
            action=target('library'), form_kind='confirm')]
    catalog = (s.get('review') or {}).get('business', {})
    result = []
    for item in catalog.get('items', []):
        props = {}; required = ['basis']; extra = {}; unavailable = None
        if item['kind'] == 'original_form':
            docs = catalog.get('documents', [])
            props = dict(decision=choice('核验结论', [dict(value='original_form',label='已核对原版商务表及业务内容')]),
                         document_id=choice('已核对的原表', [dict(value=d['document_id'],label=logical(d['path'])) for d in docs]),
                         locator=text('已核对的段落或表行位置', description='可在高级详情查看原文定位。'))
            required += list(props)
        else:
            candidates=item.get('candidates', [])
            decisions=[dict(value='real_files',label='选择真实资料')] if candidates else [dict(value='missing',label='已核实确无资料，保留文字占位')]
            props['decision']=choice('资料核验结论',decisions); required.append('decision')
            if candidates:
                opts=[dict(value=c['document_id'],label=logical(c.get('path') or c['document_id'])) for c in candidates]
                props['document_ids']=dict(type='array',title='实际采用的资料',minItems=1,uniqueItems=True,
                    items=dict(type='string',enum=[o['value'] for o in opts]),**{'x-kind':'multi_choice','x-options':opts})
                required.append('document_ids')
            elif item.get('slot_key') in s['library'].get('unresolved_keys', []):
                unavailable='资料库尚未核实此项，请在高级模式检查候选文件后重试。'
            if not item.get('sources'):
                unavailable='尚无可核验的原文出处，请在高级模式补齐来源。'
        args={k:answer(k) for k in props};args.update(item_key=item['key'],basis=answer('basis'))
        result.append(issue('business_missing',item['key'],'商务核验 · '+item['title'],
            '核对原表及真实资料。确认会留存当前账号、时间、依据和历史，随后重新生成草稿。',
            props,required,action=target('review',args,'business'),status='resolved' if item.get('review') else 'open',
            sources=item.get('sources', []),state=s,schema_extra=extra,unavailable=unavailable))
    return result


@register('uncovered')
def uncovered(s):
    result=[]
    if s.get('ssot'):
        count=len(s['ssot'].get('pending',[]));conflicts=len(s['ssot'].get('conflicts',[]))
        if count or conflicts:
            result.append(issue('rule_uncovered','ssot-fields','其他招标事实仍待核对',
                f'还有 {count} 处招标事实待确认、{conflicts} 处来源差异。请通过高级模式的字段核验处理；草稿不代表投标验收。',
                status='deferred',unavailable='本轮未接入全部招标事实的专用补录规则，请打开高级模式。'))
    return result


def build_wizard(state):
    """Pure projection, including stable IDs and declarative existing-action mappings."""
    s=deepcopy(state)
    items=[item for collector in COLLECTORS.values() for item in collector(s)]
    items.sort(key=lambda i:({'open':0,'deferred':1,'resolved':2}[i.status],
                             {'blocking':0,'warning':1,'info':2}[i.severity],i.id))
    if len({i.id for i in items}) != len(items):
        raise ValueError('问题标识重复，请核对收集器')
    next_action=None
    if not s.get('manifest'):next_action='unpack'
    elif not s.get('classified'):next_action='classify'
    elif not s.get('ssot'):next_action='ssot'
    elif not s.get('result') or s.get('stage') not in ('generate',):next_action='generate'
    opens=[i for i in items if i.status=='open']
    # A server-side failure requires an explicit retry; never run an endless auto-loop.
    retry=s.get('status') in ('failed','upload_failed','processing')
    retry_action=s.get('stage') if s.get('status')=='failed' and not opens and s.get('stage') in ('unpack','classify','ssot','generate') else None
    if retry_action=='unpack' and not s.get('archive'):retry_action=None
    stage= 'review' if opens else ('completed' if s.get('result') and not next_action else (next_action or 'review'))
    labels=dict(unpack='安全解包',classify='识别文件与物料',ssot='汇聚招标信息',generate='生成草稿',review='人工核验',completed='草稿已生成')
    done=sum(bool(s.get(k)) for k in ('manifest','classified','ssot','result'))
    resolved=sum(i.status=='resolved' for i in items)
    output=s.get('result')
    if output:
        visual=output.get('visual_validation',{})
        pages=sum(len(d.get('pages',[])) for d in visual.get('documents',[]))
        visual_label=('视觉核验记录已通过' if visual.get('visual_status')=='accepted' else
                      '已渲染，待逐页人工核验' if pages and all(d.get('status')=='rendered_pending_review' for d in visual.get('documents',[])) else
                      '部分页面渲染未完成，视觉未验收' if pages else '仅结构检查通过，视觉未验收')
        output=dict(packages=deepcopy(output.get('packages',[])),
            artifacts=[dict(path=logical(a['path'])) for a in output.get('artifacts',[])],
            warnings=[dict(type=w.get('type',''),message=(
                '招标条款或事实尚待核对，请在高级模式查看原文及差异。' if w.get('type','').startswith('ssot_') else
                '缺少需优先核对的资格、否决或限价等关键依据，请先人工核验。' if w.get('type')=='critical_field_missing' else
                human(w.get('message','请核对高级详情中的原文依据'))))
                      for w in output.get('warnings',[])],
            visual_status=visual.get('visual_status','未验收'),
            visual_label=visual_label,rendered_pages=pages,
            ready_for_submission=False)
    return dict(schema_version='tender-package-wizard/1',id=s['id'],revision=s['revision'],stage=stage,
        stage_label=labels[stage],progress=dict(done=done+resolved,total=4+len(items)),
        issues=[i.model_dump(by_alias=True) for i in items],can_auto_continue=bool(next_action and not opens and not retry),
        next_action=next_action,next_hint=('任务正在处理，请稍候刷新，勿重复提交。' if s.get('status')=='processing' else
            '上次处理未完成，请先核对待办与错误提示，或打开高级模式。' if retry else
            (f'请先完成 {len(opens)} 项人工核验。' if opens else ('将自动继续'+labels[next_action] if next_action else '草稿已生成，仍需人工验收。'))),
        retry_required=retry,retry_action=retry_action,ready_for_submission=False,
        result=output, advanced_path='/tender-package',
        test_only=bool(s.get('test_run')),regression_status='本轮跳过全量回归，仅做定向验证')
