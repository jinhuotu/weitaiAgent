"""Printable-area limits for inserted material blocks, without source text edits."""
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


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
            if len(rows)>1:
                props=rows[0].get_or_add_trPr()
                if props.find(qn('w:tblHeader')) is None:
                    props.append(OxmlElement('w:tblHeader'))


def protect_next_chapter(head):
    props=head.get_or_add_pPr()
    flag=props.find(qn('w:pageBreakBefore'))
    if flag is None:
        flag=OxmlElement('w:pageBreakBefore');props.append(flag)
    flag.set(qn('w:val'),'1')
