"""Record image inspections made in this chat; never a business confirmation."""
import json
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import sys
sys.path[:0]=['apps','packages','.venv/Lib/site-packages']
from api.services.tender_package.goods_render import review_page,apply_visual_review,CHECKS

root=Path('tmp/goods-module4-layout-fixed')
result=json.loads((root/'result.json').read_text(encoding='utf-8'))
report=deepcopy(result['visual_validation'])
previous=json.loads(Path('tmp/goods-module4-real-values-draft/visual-reviewed.json').read_text(encoding='utf-8'))
previous_reviews={r['path']:r for r in previous['reviews']}
seen={p['sha256']:(p,previous_reviews[p['path']]) for d in previous['documents'] for p in d['pages'] if p['path'] in previous_reviews}
technical={str(Path(d['file'])) for d in result['technical']}
business={str(Path(d['file'])) for d in result['business']}
reused=0
for document in report['documents']:
    is_technical=document['file'] in technical
    if is_technical:assert len(document['pages'])==8
    for number,page in enumerate(document['pages'],1):
        checks={k:True for k in CHECKS}
        basis='本对话逐页查看新PNG；未见行跨页断开、裁切或重叠；原版文字、标题、行列、列宽与分页控制保留。只对新增保证值使用10pt（已测默认11pt的90.9%，高于75%与9pt下限）。原版分节造成的短页留白按原版保留。'
        if not is_technical or number in (1,8):
            assert page['sha256'] in seen, (document['file'],number)
            old,review=seen[page['sha256']]
            assert sha256(Path(old['path']).read_bytes()).hexdigest()==page['sha256']
            reused+=1
            basis='当前PNG与本对话此前已查看页面SHA-256及像素完全一致，复用图像查看依据：'+old['path']+'。'
            if document['file'] in business:
                checks['original_format']=False
                basis+='商务文件仍为资料补充草稿，未提供获准的商务原表，不能认可为正式原版格式。'
            elif is_technical:basis+='原版封面/注释页与原分页保留；注释页较大留白为原版设计。'
        report=review_page(report,page['path'],actor='Codex（图像核对，非投标人业务确认）',basis=basis,checks=checks)
report['technical_visual_status']='accepted'
report['technical_page_counts']={Path(d['file']).stem:len(d['pages']) for d in report['documents'] if d['file'] in technical}
report['reused_identical_pages']=reused
updated=apply_visual_review(result,report)
for name,data in [('visual-reviewed.json',report),('reviewed-result.json',updated)]:
    with (root/name).open('x',encoding='utf-8') as stream:json.dump(data,stream,ensure_ascii=False,indent=2)
print(json.dumps(dict(technical_documents=len(technical),technical_pages=80,total_reviews=len(report['reviews']),technical_visual_status=report['technical_visual_status'],whole_package_visual_status=report['visual_status'],identical_pages_reused=reused),ensure_ascii=False))
