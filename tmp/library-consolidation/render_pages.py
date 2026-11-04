import json
from pathlib import Path
import fitz
from PIL import Image,ImageDraw

root=Path('tmp/library-consolidation/audit');report={}
for path in sorted(root.glob('*.pdf')):
    doc=fitz.open(path);folder=root/path.stem;folder.mkdir(exist_ok=True)
    texts=[];thumbs=[]
    for index,page in enumerate(doc):
        text=page.get_text();texts.append(text)
        pix=page.get_pixmap(matrix=fitz.Matrix(1.5,1.5),alpha=False)
        target=folder/f'page-{index+1}.png';pix.save(target)
        picture=Image.open(target);picture.thumbnail((450,620))
        tile=Image.new('RGB',(470,660),'#e5e7eb');tile.paste(picture,((470-picture.width)//2,25))
        ImageDraw.Draw(tile).text((10,5),f'{path.stem} page {index+1}',fill='black')
        thumbs.append(tile)
    for offset in range(0,len(thumbs),12):
        sheet=Image.new('RGB',(1880,1980),'#cbd5e1')
        for local,tile in enumerate(thumbs[offset:offset+12]):sheet.paste(tile,((local%4)*470,(local//4)*660))
        sheet.save(folder/f'contact-{offset//12+1}.png')
    report[path.stem]={'pages':len(doc),'emptyTextPages':[i+1 for i,text in enumerate(texts) if not text.strip()],
        'materialPages':[i+1 for i,text in enumerate(texts) if '所需材料' in text],
        'tableHeaderPages':[i+1 for i,text in enumerate(texts) if '核验内容' in text]}
    if path.stem=='layout-stress':
        body_pages=[i+1 for i,text in enumerate(texts) if '原版数据连续性核验' in text]
        assert body_pages==report[path.stem]['tableHeaderPages']
        assert len(body_pages)>=3
        assert '所需材料' in texts[-1]
    doc.close()
(root/'pages-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))
