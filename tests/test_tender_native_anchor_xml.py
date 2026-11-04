"""Plain lxml body nodes must not break business anchor discovery."""
import pytest
from lxml import etree
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from api.services.tenders.native_format import _find_body_anchor


@pytest.mark.parametrize('plain_tag', ['customBlock', qn('w:sdt'), qn('w:altChunk')])
def test_plain_lxml_block_before_heading_is_ignored(plain_tag):
    doc=Document()
    node=etree.Element(plain_tag)
    assert not hasattr(node,'xml')
    doc.element.body.insert(0,node)
    head=doc.add_paragraph('投标函')._p
    children=list(doc.element.body)
    found,cursor=_find_body_anchor(children,0,'投标函',[])
    assert found is head
    assert cursor==children.index(head)+1


def test_pageref_directory_candidate_still_excluded():
    doc=Document()
    directory=doc.add_paragraph('投标函')._p
    run=OxmlElement('w:r');field=OxmlElement('w:instrText');field.text=' PAGEREF _Toc123 '
    run.append(field);directory.append(run)
    head=doc.add_paragraph('投标函')._p
    children=list(doc.element.body)
    found,cursor=_find_body_anchor(children,0,'投标函',[])
    assert found is head
    assert cursor==children.index(head)+1


def test_plain_node_no_matching_heading_keeps_cursor():
    node=etree.Element('customBlock')
    assert _find_body_anchor([node],0,'投标函',[])==(None,0)

def test_business_contexts_reads_form_past_plain_lxml_node():
    from api.services.tenders.business_draft_policy import business_contexts
    from api.services.tenders.schema import BidBrief, OutlineItem
    doc=Document()
    doc.element.body.insert(0,etree.Element(qn('w:altChunk')))
    doc.add_paragraph('专项承诺书',style='Heading 1')
    doc.add_paragraph('供应商须承诺按期交货。')
    brief=BidBrief(outlineItems=[OutlineItem(id='promise',title='专项承诺书',kind='commitment_copy')])
    assert '供应商须承诺按期交货。' in business_contexts(brief,doc=doc)['专项承诺书']
