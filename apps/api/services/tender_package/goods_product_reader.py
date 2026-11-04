"""Read supplier value columns and local identifier evidence, never tender demands."""
import re
from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph
from .classify import ID_PATTERN
from .ssot import norm


def read_parameters(path,*,infer_bindings=False):
    document=Document(path);parameters=[];active=[];evidence=None;ti=0;pi=0
    for element in document.element.body:
        if element.tag.endswith('}p'):
            pi+=1;text=Paragraph(element,document).text
            ids=sorted(set(v.replace('－','-') for v in re.findall(ID_PATTERN,text)))
            if ids:
                active=ids;evidence=dict(locator=f'paragraph:{pi}',quote=text)
            continue
        if not element.tag.endswith('}tbl'):continue
        ti+=1;table=Table(element,document);header=None
        for ri,row in enumerate(table.rows,1):
            cells=[c.text for c in row.cells]
            labels=[i for i,c in enumerate(cells) if re.fullmatch(r'参数名称|性能项目|项目名称|参数|名称',norm(c))]
            values=[i for i,c in enumerate(cells) if re.fullmatch(r'(?:投标人|供应商)?(?:保证值|实际值|标称值|实测值|检测值|参数值|产品参数值)',norm(c))]
            units=[i for i,c in enumerate(cells) if re.fullmatch(r'单位|计量单位',norm(c))]
            if len(labels)==len(values)==1:
                header=(labels[0],values[0],units[0] if len(units)==1 else None, cells[values[0]])
                continue
            if not header:continue
            li,vi,ui,value_header=header
            if max(li,vi,ui or 0)>=len(cells) or not cells[li].strip() or not cells[vi].strip():continue
            item=dict(label=cells[li],value=cells[vi],unit=cells[ui] if ui is not None else None,
                      locator=f'table:{ti}/row:{ri}',raw_cells=cells,value_header=value_header,
                      evidence_kind='supplier_declared' if '保证' in value_header else 'supplier_parameter')
            if infer_bindings:
                item['solidified_ids']=list(active) if len(active)==1 else []
                item['binding_evidence']=evidence
            parameters.append(item)
    return parameters
