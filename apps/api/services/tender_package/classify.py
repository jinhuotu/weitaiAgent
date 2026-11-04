"""Evidence-based package classification, independent of tender generation."""
from copy import deepcopy
from collections import Counter
from hashlib import sha256
from pathlib import Path
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

ROLES = {'invitation', 'notice', 'tech_spec', 'contract', 'quotation', 'commitment', 'integrity', 'other'}
ID_PATTERN = r'(?<!\d)\d{3,8}(?:[-－]\d{3,12}){1,3}(?!\d)'
RULES = {
    'invitation': r'第\s*[一1]\s*章|投标邀请|邀请书|invitation',
    'notice': r'投标人须知|招标正文|第\s*[二三四五六2-6]\s*(?:章|[-至到]\s*[二三四五六2-6]\s*章)',
    'tech_spec': r'技术规范|技术规格|technical\s+specification|固化\s*ID',
    'contract': r'合同文件|采购合同|合同协议|通用条款|专用条款|协议书|contract',
    'quotation': r'货物清单|报价表|价格表|分项报价|quotation|price\s+schedule',
    'commitment': r'(?:诚信|廉洁|合规|反商业贿赂|守法)[^\n]{0,16}(?:承诺|声明)|(?:承诺|声明)[^\n]{0,16}(?:诚信|廉洁|合规|反商业贿赂|守法)',
}


def read_content(path, max_bytes=16*1024*1024):
    """Bound XML/text reads; unsupported formats remain explicitly reviewable."""
    suffix = path.suffix.lower()
    if suffix == '.docx':
        with zipfile.ZipFile(path) as z:
            info = z.getinfo('word/document.xml')
            if info.file_size > max_bytes:
                raise ValueError('正文 XML 超出分类读取上限')
            root = ET.fromstring(z.read(info))
            ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
            return '\n'.join(''.join(t.text or '' for t in p.iter(ns+'t')) for p in root.iter(ns+'p'))
    if suffix in ('.txt', '.csv'):
        if path.stat().st_size > max_bytes:
            raise ValueError('正文超出分类读取上限')
        data = path.read_bytes()
        for encoding in ('utf-8-sig', 'gb18030'):
            try: return data.decode(encoding)
            except UnicodeError: pass
        raise ValueError('正文编码无法识别')
    raise ValueError('此格式尚无离线正文读取器；请人工确认角色或提供文本')


def classify(manifest, root, *, overrides=None, content_reader=read_content):
    """Return a copy; preserve every manifest entry and its provenance.

    overrides: {storage_path: {role, reason, actor}} for durable human correction.
    """
    result = deepcopy(manifest)
    base = Path(root).resolve()
    mappings, pending = [], []
    for file in result['files']:
        reasons, candidates = [], []
        suffix = file['file_type'].lower()
        name = unicodedata.normalize('NFKC', Path(file['logical_path']).name)
        text = ''
        if suffix == '.sign' or file.get('role') == 'integrity':
            role = 'integrity'; reasons.append('签名/完整性校验文件，不参与正文')
        elif suffix in ('.zip', '.rar', '.7z'):
            role = 'other'; reasons.append('压缩容器，仅保留来源关系；角色由内部文档判定')
        else:
            path = (base / file['storage_path']).resolve()
            try:
                if not path.is_relative_to(base) or not path.is_file():
                    raise ValueError('文件路径越界或文件不存在')
                if sha256(path.read_bytes()).hexdigest() != file['sha256']:
                    raise ValueError('文件哈希不符；请重新解包')
                text = unicodedata.normalize('NFKC', content_reader(path))
            except Exception as exc:
                reasons.append(f'正文读取未完成：{type(exc).__name__}；请人工核对')
            # Filename is a document title. Body evidence uses leading nonempty lines,
            # avoiding role keywords merely mentioned in a long tender's contents.
            heading = '\n'.join(x.strip() for x in text.splitlines() if x.strip())[:1200]
            named = [r for r, pattern in RULES.items() if re.search(pattern, name, re.I)]
            body = [r for r, pattern in RULES.items() if re.search(pattern, heading[:300], re.I)]
            candidates = named or body
            reasons.extend(f'文件名命中 {r}' for r in named)
            reasons.extend(f'正文开头命中 {r}' for r in body)
            # A filename may resolve an omnibus body, but contradictory single-role
            # headings require review instead of silently accepting the filename.
            conflict = len(body) == 1 and len(named) == 1 and body != named
            role = candidates[0] if len(candidates) == 1 and not conflict else 'other'
            if role == 'other': reasons.append('证据不足或多角色冲突，需人工确认')
        override = (overrides or {}).get(file['storage_path'])
        if override:
            if override.get('role') not in ROLES or not override.get('reason') or not override.get('actor'):
                raise ValueError('人工纠正需提供合法 role、reason、actor')
            if suffix == '.sign' and override['role'] != 'integrity':
                raise ValueError('签名文件不能改为正文角色')
            role = override['role']; file['manual_override'] = deepcopy(override)
            reasons.append('人工纠正：'+override['reason'])
        file.update(role=role, role_evidence=reasons, role_candidates=candidates)
        ids = []
        if role == 'tech_spec':
            # Only labelled IDs or IDs in a technical specification filename qualify.
            ids = re.findall(ID_PATTERN, name)
            ids += re.findall(r'固化\s*ID\s*[:：编码\s（(]*('+ID_PATTERN+')', text, re.I)
            ids = sorted(set(x.replace('－','-') for x in ids))
        file['solidified_ids'] = ids
        materials = re.findall(r'(?:物料名称|物资名称|货物名称|设备名称|产品名称)\s*[:：]\s*([^\n]{1,160})', text)
        if override and override.get('material'):
            materials = [override['material']]
        file['material_candidates'] = list(dict.fromkeys(materials))
        for code in ids:
            mappings.append(dict(solidified_id=code, logical_path=file['logical_path'],
                                 storage_path=file['storage_path'], sha256=file['sha256'],
                                 material=materials[0] if len(set(materials)) == 1 else None,
                                 material_candidates=file['material_candidates'],
                                 needs_confirmation=len(set(materials)) != 1))
        if role == 'other': pending.append(file['storage_path'])
    groups = {}
    for file in result['files']:
        groups.setdefault(file['role'], []).append(file)
    for group in groups.values():
        for index, file in enumerate(group):
            file['role_order'] = index + 1
            file['primary_candidate'] = index == 0
    result.update(role_counts=dict(Counter(f['role'] for f in result['files'])),
                  solidified_id_map=mappings, pending_confirmation=pending,
                  classification_warnings=[] if groups.get('invitation') else ['未识别邀请书；请人工确认或补充文件'])
    from .materials import enrich_materials
    return enrich_materials(result, base)
