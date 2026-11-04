"""Opt-in layout repair for module-four copies, never source templates."""
from copy import deepcopy
from hashlib import sha256
import math
import json
from pathlib import Path
import shutil
import zipfile
from lxml import etree

W='{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'


def _property(parent,name,value):
    child=parent.find(W+name)
    if child is None:child=etree.SubElement(parent,W+name)
    child.set(W+'val',value)


def _ensure(parent,name):
    child=parent.find(W+name)
    if child is None:
        child=etree.Element(W+name);parent.insert(0,child)
    return child


def repair_technical_layout(source,destination,filled_cells,*,font_scale=1.0,
                            min_readable_pt=9.0,rendered_default_pt=None,
                            default_font_basis=None,expected_sha256=None):
    """Only cantSplit, fixed original widths, wrapping and new-value font size.

    A document without font defaults needs an explicitly measured renderer font,
    with its measurement basis. No source title, text, row, column or width changes.
    """
    source=Path(source);destination=Path(destination)
    if destination.exists() or source.resolve()==destination.resolve():raise ValueError('排版修复必须输出新的文件')
    if not .75<=font_scale<=1 or min_readable_pt<=0:raise ValueError('字号比例不得低于75%，可读下限须为正数')
    before_hash=sha256(source.read_bytes()).hexdigest()
    if expected_sha256 and expected_sha256!=before_hash:raise ValueError('待修复文档哈希不符')
    cells={(c['table'],c['row'],c['column']) for c in filled_cells}
    with zipfile.ZipFile(source) as archive:
        tree=etree.fromstring(archive.read('word/document.xml'),etree.XMLParser(resolve_entities=False,no_network=True));original=deepcopy(tree)
        tables=tree.find(W+'body').findall(W+'tbl');edits=[];fonts=[];located=set()
        # Save exact property nodes, then reverse them for a structural proof.
        def save(parent,name):
            old=parent.find(W+name)
            edits.append((parent,name,deepcopy(old) if old is not None else None))
        for ti,table in enumerate(tables,1):
            save(table,'tblPr');pr=_ensure(table,'tblPr')
            layout=pr.find(W+'tblLayout')
            if layout is None:layout=etree.SubElement(pr,W+'tblLayout')
            layout.set(W+'type','fixed')
            for ri,row in enumerate(table.findall(W+'tr'),1):
                save(row,'trPr');_property(_ensure(row,'trPr'),'cantSplit','1')
                for ci,cell in enumerate(row.findall(W+'tc'),1):
                    if (ti,ri,ci) not in cells:continue
                    located.add((ti,ri,ci))
                    save(cell,'tcPr');_property(_ensure(cell,'tcPr'),'noWrap','0')
                    for paragraph in cell.findall(W+'p'):
                        save(paragraph,'pPr');_property(_ensure(paragraph,'pPr'),'wordWrap','1')
                        paragraph_sizes=[]
                        for run in paragraph.findall(W+'r'):
                            if not run.findall(W+'t'):continue
                            save(run,'rPr');rp=_ensure(run,'rPr');sz=rp.find(W+'sz')
                            original_pt=float(sz.get(W+'val'))/2 if sz is not None else None
                            if original_pt is None:
                                if rendered_default_pt is None or not default_font_basis:raise ValueError('原字号未明确；须提供已核对的渲染默认字号及依据')
                                original_pt=float(rendered_default_pt)
                            if original_pt<min_readable_pt:raise ValueError('原字号低于配置可读下限，需人工确认，禁止无依据缩放')
                            points=math.ceil(max(original_pt*.75,min_readable_pt,original_pt*font_scale)*2)/2
                            if points>original_pt:raise ValueError('字号量化超出原字号，请核对字号依据')
                            for name in ('sz','szCs'):_property(rp,name,str(round(points*2)))
                            paragraph_sizes.append(points)
                            fonts.append(dict(table=ti,row=ri,column=ci,original_pt=original_pt,
                                              selected_pt=points,ratio=points/original_pt,
                                              default_font_basis=default_font_basis if sz is None else None))
                        if paragraph_sizes:
                            mark=_ensure(paragraph.find(W+'pPr'),'rPr')
                            for name in ('sz','szCs'):_property(mark,name,str(round(max(paragraph_sizes)*2)))
        if located!=cells:raise ValueError('排版指定位置不存在，已中止')
        edited=etree.tostring(tree,xml_declaration=True,encoding='UTF-8',standalone=True)
        for parent,name,old in reversed(edits):
            current=parent.find(W+name)
            if current is not None:parent.remove(current)
            if old is not None:parent.insert(0,old)
        # Original properties are first children in OOXML. Verify the entire tree.
        if etree.tostring(tree,method='c14n')!=etree.tostring(original,method='c14n'):
            raise ValueError('允许的排版属性以外有变化，已中止')
        destination.parent.mkdir(parents=True,exist_ok=True)
        with zipfile.ZipFile(destination,'x') as output:
            for info in archive.infolist():output.writestr(info,edited if info.filename=='word/document.xml' else archive.read(info.filename))
    return dict(source_sha256=before_hash,output_sha256=sha256(destination.read_bytes()).hexdigest(),
                text_structure_verified=True,unchanged_other_members=True,rows_cannot_split=True,
                original_widths_preserved=True,font_scale=font_scale,min_readable_pt=min_readable_pt,fonts=fonts)


def repair_goods_package(source_root,destination,*,layout_options,renderer=None):
    """Independent post-processing entry; the original generator is unchanged."""
    from .goods_render import render_package
    source_root=Path(source_root).resolve();destination=Path(destination).resolve()
    if destination.exists() or source_root.is_relative_to(destination):raise ValueError('排版输出必须是新的独立目录')
    result=json.loads((source_root/'result.json').read_text(encoding='utf-8'))
    expected={a['path']:a for a in result['artifacts']}
    sources=[]
    for kind in ('technical','business','price'):
        for item in result[kind]:
            source=Path(item['file']).resolve()
            if not source.is_relative_to(source_root) or not source.is_file():raise ValueError('产物路径不存在或越界')
            relative=source.relative_to(source_root)
            if str(relative) not in expected or sha256(source.read_bytes()).hexdigest()!=expected[str(relative)]['sha256']:raise ValueError('产物哈希变化，须先重新生成')
            sources.append((kind,item,source,relative))
    destination.mkdir(parents=True,exist_ok=False)
    updated=deepcopy(result);updated['previous_artifacts']=deepcopy(result['artifacts'])
    lookup={(kind,item['file']):item for kind in ('technical','business','price') for item in updated[kind]}
    for kind,item,source,relative in sources:
        target=destination/relative;target.parent.mkdir(parents=True,exist_ok=True)
        revised=lookup[(kind,item['file'])]
        if kind=='technical':
            revised['layout_proof']=repair_technical_layout(source,target,item['proof']['applied'],
                expected_sha256=expected[str(relative)]['sha256'],**layout_options)
        else:shutil.copy2(source,target)
        revised['file']=str(target)
    updated['layout_options']=deepcopy(layout_options)
    updated.setdefault('visual_history',[]).append(deepcopy(result['visual_validation']))
    updated['visual_validation']=render_package(destination,renderer)
    updated['warnings']=[w for w in updated['warnings'] if w.get('type') not in ('visual_not_accepted','visual_review_issue')]
    updated['warnings'].append(dict(type='visual_not_accepted',level='critical',source=None,
                                   message='已按授权修复排版，须对新文件逐页核对，不沿用旧页验收'))
    updated['warnings'].sort(key=lambda w:0 if w.get('level')=='critical' else 1)
    updated['artifacts']=[dict(path=str(p.relative_to(destination)),size=p.stat().st_size,sha256=sha256(p.read_bytes()).hexdigest()) for p in sorted(destination.rglob('*.docx'))]
    updated['ready_for_submission']=False
    with (destination/'result.json').open('x',encoding='utf-8') as stream:json.dump(updated,stream,ensure_ascii=False,indent=2)
    return updated


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description='独立技术表排版修复，保留旧产物并重新渲染')
    parser.add_argument('--input-package',required=True);parser.add_argument('--output',required=True)
    parser.add_argument('--options',required=True,help='排版参数JSON，含字号测量依据')
    args=parser.parse_args()
    try:
        report=repair_goods_package(args.input_package,args.output,layout_options=json.loads(Path(args.options).read_text(encoding='utf-8-sig')))
        print(json.dumps(dict(documents=len(report['visual_validation']['documents']),pages=sum(len(d['pages']) for d in report['visual_validation']['documents'])),ensure_ascii=True))
    except (ValueError,OSError,KeyError,zipfile.BadZipFile) as exc:parser.exit(2,'排版修复未完成：'+str(exc)+'；旧文件保持不变，请核对新输出及配置。\n')
