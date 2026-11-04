"""Unified optional renderers. Rendering is not visual acceptance."""
from copy import deepcopy
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone
from typing import Protocol
import shutil
import subprocess
import os


def discover_libreoffice(executable=None):
    """Discover local installations only; explicit configuration stays authoritative."""
    if executable:
        return shutil.which(executable) or (str(Path(executable)) if Path(executable).is_file() else None)
    for name in ('soffice', 'libreoffice'):
        found=shutil.which(name)
        if found:return found
    for variable in ('ProgramFiles', 'ProgramFiles(x86)', 'LOCALAPPDATA'):
        directory=os.environ.get(variable)
        if directory:
            candidate=Path(directory)/'LibreOffice'/'program'/'soffice.exe'
            if candidate.is_file():return str(candidate)
    return None


class Renderer(Protocol):
    def available(self) -> bool: ...
    def render(self,document:Path,output:Path) -> list[Path]: ...


@dataclass
class CommandRenderer:
    command: list[str]
    timeout: int = 120

    def available(self):
        return bool(self.command and (Path(self.command[0]).is_file() or shutil.which(self.command[0])))

    def render(self,document,output):
        output.mkdir(parents=True,exist_ok=False)
        args=[v.replace('{input}',str(document.resolve())).replace('{output}',str(output.resolve())) for v in self.command]
        result=subprocess.run(args,shell=False,capture_output=True,timeout=self.timeout)
        if result.returncode:raise ValueError('渲染命令失败，请检查渲染器日志或配置')
        return sorted(output.glob('*.png'))


@dataclass
class LibreOfficeRenderer:
    executable: str | None = None
    timeout: int = 120
    scale: float = 1.5

    def available(self):
        return bool(discover_libreoffice(self.executable))

    def render(self,document,output):
        import pypdfium2 as pdfium
        executable=discover_libreoffice(self.executable)
        if not executable:raise ValueError('未找到本地LibreOffice，请配置渲染器路径')
        output.mkdir(parents=True,exist_ok=False)
        profile=(output/'profile').resolve().as_uri()
        result=subprocess.run([executable,'-env:UserInstallation='+profile,'--headless','--convert-to','pdf','--outdir',str(output.resolve()),str(document.resolve())],shell=False,capture_output=True,timeout=self.timeout)
        pdf=output/(document.stem+'.pdf')
        if result.returncode or not pdf.is_file():raise ValueError('文档转PDF失败；请检查配置或改用兼容渲染器')
        pages=[]
        with pdfium.PdfDocument(str(pdf)) as doc:
            for index in range(len(doc)):
                page=doc[index];bitmap=page.render(scale=self.scale);image=bitmap.to_pil()
                path=output/f'page-{index+1:04d}.png';image.save(path);pages.append(path)
                image.close();bitmap.close();page.close()
        return pages


def render_package(package_root,renderer=None):
    """Every DOCX is tracked; unavailable/failed renders never mean passed."""
    root=Path(package_root).resolve();files=sorted(root.rglob('*.docx'))
    report=dict(structure_only=True,visual_status='not_accepted',documents=[],reviews=[])
    if renderer is None:renderer=LibreOfficeRenderer()
    try:available=renderer.available()
    except Exception:available=False
    if not available:
        report['documents']=[dict(file=str(document),sha256=sha256(document.read_bytes()).hexdigest(),pages=[],status='not_rendered') for document in files]
        report['message']='仅结构检查通过、视觉未验收：没有可用的已配置渲染器'
        return report
    for index,document in enumerate(files):
        item=dict(file=str(document),sha256=sha256(document.read_bytes()).hexdigest(),pages=[],status='failed')
        try:
            pages=renderer.render(document,root/'visual'/str(index))
            if not pages:raise ValueError('渲染器没有生成页面PNG')
            for page in pages:
                path=Path(page).resolve()
                if not path.is_relative_to(root/'visual'/str(index)) or path.is_symlink() or path.suffix.lower()!='.png':raise ValueError('渲染页面路径不合法')
                from PIL import Image
                with Image.open(path) as image:image.verify()
                item['pages'].append(dict(path=str(path),sha256=sha256(path.read_bytes()).hexdigest()))
            item['status']='rendered_pending_review'
        except Exception as exc:
            item['message']='渲染未完成：'+type(exc).__name__+'；请检查渲染器或文件，不能视作视觉通过'
        report['documents'].append(item)
    report['structure_only']=False
    report['message']='已生成的页面仍须逐页人工核对；未渲染页保持未验收'
    return report


CHECKS={'layout','pagination','alignment','blank_regions','original_format'}


def review_page(report,path,*,actor,basis,checks):
    if not actor.strip() or not basis.strip() or set(checks)!=CHECKS or any(not isinstance(v,bool) for v in checks.values()):raise ValueError('须填写核对人、依据及全部逐页检查项')
    result=deepcopy(report);matches=[(d,p) for d in result['documents'] for p in d['pages'] if p['path']==str(path)]
    if len(matches)!=1:raise ValueError('页面不在当前渲染报告中')
    document,page=matches[0]
    if sha256(Path(document['file']).read_bytes()).hexdigest()!=document['sha256']:raise ValueError('文档已变更，须重新渲染')
    if sha256(Path(path).read_bytes()).hexdigest()!=page['sha256']:raise ValueError('页面哈希不符，须重新渲染')
    result['reviews'].append(dict(path=str(path),sha256=page['sha256'],actor=actor,basis=basis,checks=deepcopy(checks),confirmed_at=datetime.now(timezone.utc).isoformat()))
    latest={r['path']:r for r in result['reviews']}
    all_pages=[p for d in result['documents'] for p in d['pages']]
    ready=bool(all_pages) and all(d['status']=='rendered_pending_review' for d in result['documents']) and all(p['path'] in latest and all(latest[p['path']]['checks'].values()) for p in all_pages)
    if ready:
        ready=all(Path(d['file']).is_file() and sha256(Path(d['file']).read_bytes()).hexdigest()==d['sha256'] for d in result['documents']) and all(Path(p['path']).is_file() and sha256(Path(p['path']).read_bytes()).hexdigest()==p['sha256'] for p in all_pages)
    result['visual_status']='accepted' if ready else 'not_accepted'
    return result


def apply_visual_review(result,report):
    """Attach a reviewed report and its failed checks to external warnings only."""
    expected={str(Path(item['file']).resolve()) for key in ('technical','business','price') for item in result[key]}
    actual={str(Path(item['file']).resolve()) for item in report['documents']}
    if actual!=expected or len(actual)!=len(report['documents']):raise ValueError('视觉报告与本次全部产物不一致')
    for document in report['documents']:
        if sha256(Path(document['file']).read_bytes()).hexdigest()!=document['sha256']:raise ValueError('文档已变更，须重新渲染')
        for page in document['pages']:
            if sha256(Path(page['path']).read_bytes()).hexdigest()!=page['sha256']:raise ValueError('页面已变更，须重新渲染')
    updated=deepcopy(result)
    updated.setdefault('visual_history',[]).append(deepcopy(updated['visual_validation']))
    updated['visual_validation']=deepcopy(report)
    updated['warnings']=[w for w in updated['warnings'] if w.get('type') not in ('visual_not_accepted','visual_review_issue')]
    latest={review['path']:review for review in report['reviews']}
    for document in report['documents']:
        for index,page in enumerate(document['pages'],1):
            review=latest.get(page['path'])
            failures=sorted(k for k,v in review['checks'].items() if not v) if review else sorted(CHECKS)
            if failures:
                updated['warnings'].append(dict(type='visual_review_issue',level='critical',source=page['path'],
                    message='页面尚未通过全部视觉检查，请核对原格式和分页；未修改正文以规避问题',
                    document=document['file'],page=index,failed_checks=failures,review=deepcopy(review)))
    if report['visual_status']!='accepted':
        updated['warnings'].append(dict(type='visual_not_accepted',level='critical',source=None,
                                       message='视觉验收尚未通过，请查看逐页核对记录及未通过项'))
    updated['warnings'].sort(key=lambda w:0 if w.get('level')=='critical' else 1)
    updated['ready_for_submission']=False
    return updated



def renderer_from_config(config):
    registry={'libreoffice':LibreOfficeRenderer,'command':CommandRenderer}
    if not config:return LibreOfficeRenderer()
    kind=config.get('kind')
    if kind not in registry:raise ValueError('渲染适配类型无效，请配置已注册的适配器')
    return registry[kind](**config.get('options',{}))
