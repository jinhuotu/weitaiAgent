"""Move evaluated Python string values, not source-code quoting or indentation."""
import ast
import hashlib
import json
from pathlib import Path

root = Path('apps/api/services/tenders')
dest = root / 'prompts'
dest.mkdir(exist_ok=True)
assignments = {
    'extract': {'_SYSTEM': 'extract_system'},
    'tech_draft': {'_DRAFT_SYSTEM': 'tech_draft_system'},
    'tech_fill': {'_BOQ_SYSTEM': 'boq_system', '_DRAW_SYSTEM': 'draw_system'},
    'qa': {'_SYSTEM': 'qa_system'},
}
inline = {'gen_biz_resp': ['biz_resp_system', 'biz_resp_compose_system'],
          'gen_tech_plan': ['tech_plan_system'],
          'facts': ['facts_system'], 'performance': ['performance_system']}
manifest = {}
for module in [*assignments, *inline]:
    path = root / (module + '.py')
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    edits = []
    candidates = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and module in assignments:
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in assignments[module]:
                    candidates.append((node.value, assignments[module][target.id]))
    systems = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            pairs = {k.value: v for k,v in zip(node.keys,node.values) if isinstance(k,ast.Constant)}
            if isinstance(pairs.get('role'),ast.Constant) and pairs['role'].value == 'system':
                if isinstance(pairs.get('content'),(ast.Constant,ast.JoinedStr)):
                    systems.append(pairs['content'])
            if module == 'extract' and isinstance(pairs.get('content'),ast.Constant) and (
                isinstance(pairs.get('role'),ast.Constant) and pairs['role'].value == 'user'
            ):
                candidates.append((pairs['content'], 'extract_retry'))
    systems.sort(key=lambda n:n.lineno)
    if module in inline:
        assert len(systems) == len(inline[module])
        candidates.extend(zip(systems,inline[module]))
    for node,name in candidates:
        if isinstance(node,ast.JoinedStr):
            variables={'_ROUTE_LABELS[route]':'route_label','_ROUTE_TOPICS[route]':'route_topics'}
            text=''
            for part in node.values:
                if isinstance(part,ast.Constant): text+=part.value
                else:
                    assert part.conversion == -1 and part.format_spec is None
                    text+='$'+variables[ast.unparse(part.value)]
            values = {}
            env={}
            for assign in tree.body:
                if isinstance(assign,ast.Assign) and isinstance(assign.targets[0],ast.Name) and assign.targets[0].id in {'_ROUTE_LABELS','_ROUTE_TOPICS'}:
                    env[assign.targets[0].id]=ast.literal_eval(assign.value)
            for route in env['_ROUTE_LABELS']:
                values[route]=eval(compile(ast.Expression(node),'<original-prompt>','eval'),{**env,'route':route})
            manifest[name]={'template':text,'originalByRoute':values}
            replacement='get_prompt("tech_plan_system", route_label=_ROUTE_LABELS[route], route_topics=_ROUTE_TOPICS[route])'
        else:
            text=ast.literal_eval(node)
            assert isinstance(text,str) and '$' not in text
            manifest[name]={'original':text,'sha256':hashlib.sha256(text.encode('utf-8')).hexdigest()}
            replacement=f'get_prompt("{name}")'
        (dest/(name+'.md')).write_text(text,encoding='utf-8',newline='')
        edits.append((node,replacement))
    # AST columns are UTF-8 byte offsets, so splice byte lines to preserve Chinese.
    lines=source.encode('utf-8').splitlines(keepends=True)
    starts=[0]
    for line in lines: starts.append(starts[-1]+len(line))
    data=source.encode('utf-8')
    for node,replacement in sorted(edits,key=lambda e:(e[0].lineno,e[0].col_offset),reverse=True):
        a=starts[node.lineno-1]+node.col_offset
        b=starts[node.end_lineno-1]+node.end_col_offset
        data=data[:a]+replacement.encode()+data[b:]
    source=data.decode('utf-8')
    anchor='from __future__ import annotations\n'
    source=source.replace(anchor,anchor+'\nfrom api.services.tenders.prompts import get_prompt\n',1)
    path.write_text(source,encoding='utf-8',newline='')
Path('tmp/prompt-migration/manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
print('Migrated',len(manifest),'prompts')
