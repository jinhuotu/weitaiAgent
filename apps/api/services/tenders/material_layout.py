"""Printable-area limits for inserted material blocks, without source text edits."""
import re
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def _column_header(row):
    cells = row.findall(qn('w:tc'))
    if len(cells) < 2:
        return False
    labels = [''.join(node.text or '' for node in cell.iter(qn('w:t'))).strip() for cell in cells]
    # Blank/value fields form a questionnaire, not a repeating column header.
    plausible = all(1 <= len(label) <= 40 and re.sub(r'[\s_＿:：]', '', label)
                    and not re.search(r'[。；;！？!?：:]|^\d|[_＿]{2}', label) for label in labels)
    if not plausible:
        return False
    label_shape = r'序号|编号|名称|项目|内容|规格|型号|单位|数量|单价|合价|金额|备注|姓名|职务|岗位|时间|日期|参数|维度|属性|状态|结果|说明|范围|要求|响应|偏差|边界'
    semantic = sum(bool(re.search(label_shape, label)) for label in labels) >= 2
    styled = all(any(run.find('w:rPr/w:b', run.nsmap) is not None for run in cell.iter(qn('w:r')))
                 for cell in cells)
    return semantic or styled


def repeating_header_rows(table):
    rows = table.findall(qn('w:tr'))
    if len(rows) < 2:
        return []
    if _column_header(rows[0]):
        if len(rows) > 2 and any(int(span.get(qn('w:val'), '1')) > 1
                                for span in rows[0].iter(qn('w:gridSpan'))) and _column_header(rows[1]):
            return rows[:2]
        return [rows[0]]
    # A full-width group label can precede a true column header. Repeated
    # headers must start at row one in Word, so preserve that whole header band.
    cells = rows[0].findall(qn('w:tc'))
    title = ''.join(node.text or '' for node in rows[0].iter(qn('w:t'))).strip()
    if len(cells) == 1 and 0 < len(title) <= 40 and _column_header(rows[1]):
        return rows[:2]
    return []


def mark_repeating_headers(doc):
    """Only add tblHeader metadata; never rewrite original text or layout."""
    count = 0
    for table in doc.element.body.iter(qn('w:tbl')):
        for row in repeating_header_rows(table):
            props = row.get_or_add_trPr()
            flag = props.find(qn('w:tblHeader'))
            if flag is None:
                props.append(OxmlElement('w:tblHeader'))
                count += 1
            elif flag.get(qn('w:val'), '1') in {'0', 'false', 'off'}:
                flag.set(qn('w:val'), '1')
                count += 1
    return count


def printable_twips(doc, anchor):
    current = anchor
    props = None
    while current is not None:
        props = current.find('w:pPr/w:sectPr',current.nsmap)
        if props is not None:
            break
        current = current.getnext()
    if props is None:
        props = doc.element.body.find(qn('w:sectPr'))
    section=doc.sections[-1]
    width=int((section.page_width-section.left_margin-section.right_margin)/635)
    height=int((section.page_height-section.top_margin-section.bottom_margin)/635)
    if props is not None:
        size=props.find(qn('w:pgSz'));margins=props.find(qn('w:pgMar'))
        if size is not None and margins is not None:
            width=int(size.get(qn('w:w')))-int(margins.get(qn('w:left'),'0'))-int(margins.get(qn('w:right'),'0'))
            height=int(size.get(qn('w:h')))-int(margins.get(qn('w:top'),'0'))-int(margins.get(qn('w:bottom'),'0'))
    return max(1,width),max(1,height-284)  # reserve caption/bottom clearance


def constrain_blocks(doc, anchor, blocks):
    width,height=printable_twips(doc,anchor)
    for block in blocks:
        for extent in block.iter(qn('wp:extent')):
            x,y=int(extent.get('cx','0')),int(extent.get('cy','0'))
            if not x or not y:
                continue
            ratio=min(1,width*635/x,height*635/y)
            extent.set('cx',str(int(x*ratio)));extent.set('cy',str(int(y*ratio)))
            drawing=next(extent.iterancestors(qn('w:drawing')),None)
            if drawing is not None:
                for transform in drawing.iter(qn('a:xfrm')):
                    size=transform.find(qn('a:ext'))
                    if size is not None:
                        size.set('cx',str(int(x*ratio)));size.set('cy',str(int(y*ratio)))
        for table in block.iter(qn('w:tbl')):
            grid=table.find(qn('w:tblGrid'))
            columns=list(grid) if grid is not None else []
            total=sum(int(col.get(qn('w:w'),'0')) for col in columns)
            if total>width:
                ratio=width/total
                table_width=table.find('w:tblPr/w:tblW',table.nsmap)
                if table_width is not None:
                    table_width.set(qn('w:type'),'dxa');table_width.set(qn('w:w'),str(width))
                for col in columns:
                    col.set(qn('w:w'),str(max(1,int(int(col.get(qn('w:w'),'0'))*ratio))))
                for cell_width in table.iter(qn('w:tcW')):
                    if cell_width.get(qn('w:type'),'dxa')=='dxa':
                        cell_width.set(qn('w:w'),str(max(1,int(int(cell_width.get(qn('w:w'),'0'))*ratio))))
            rows=table.findall(qn('w:tr'))
            for row in rows:
                props=row.find(qn('w:trPr'))
                if props is not None:
                    for flag in list(props.findall(qn('w:cantSplit'))):props.remove(flag)
                    row_height=props.find(qn('w:trHeight'))
                    if row_height is not None and int(row_height.get(qn('w:val'),'0'))>height:
                        row_height.set(qn('w:hRule'),'atLeast')
                        row_height.set(qn('w:val'),str(height))
            for row in repeating_header_rows(table):
                props=row.get_or_add_trPr()
                if props.find(qn('w:tblHeader')) is None:
                    props.append(OxmlElement('w:tblHeader'))


def protect_next_chapter(head):
    props=head.get_or_add_pPr()
    flag=props.find(qn('w:pageBreakBefore'))
    if flag is None:
        flag=OxmlElement('w:pageBreakBefore');props.append(flag)
    flag.set(qn('w:val'),'1')
