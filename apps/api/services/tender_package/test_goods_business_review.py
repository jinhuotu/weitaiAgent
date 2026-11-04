from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import pytest
from .test_goods_adapter import sample
from .goods_inputs import create_input_session
from .goods_business_review import confirm_business,business_catalog


def context(tmp_path):
    ssot=sample(tmp_path);session=create_input_session(ssot);doc=ssot.documents[0]
    source=doc.blocks[0].model_dump();source.update(document_id=doc.document_id)
    draft=dict(business=[dict(package_key='P',outcomes=[dict(key='slot',request_title='真实资料要求',ssot_sources=[source])])])
    library=dict(loaded=True,slots=[],candidate_media={'slot':[]},media={'slot':[]},unresolved_keys=[])
    return ssot,session,draft,library


def test_no_default_decision_or_sourceless_confirmation(tmp_path):
    s,state,draft,lib=context(tmp_path);args=dict(item_key='P/slot',actor='人',basis='核验记录')
    with pytest.raises(ValueError,match='明确选择'):confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    args.update(decision='missing',actor='')
    with pytest.raises(ValueError,match='确认人'):confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    args['actor']='人';draft['business'][0]['outcomes'][0]['ssot_sources']=[]
    with pytest.raises(ValueError,match='无源'):confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    assert not state.get('business_review_history')


def test_existing_file_cannot_be_called_missing_and_selection_traced(tmp_path):
    s,state,draft,lib=context(tmp_path);p=tmp_path/'real.png';p.write_bytes(b'actual fixture')
    lib['candidate_media']['slot']=[dict(document_id='real',path=str(p),sha256=sha256(p.read_bytes()).hexdigest())]
    args=dict(item_key='P/slot',decision='missing',actor='人',basis='逐项核对')
    with pytest.raises(ValueError,match='真实候选'):confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    args.update(decision='real_files',document_ids=['real'])
    result=confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    assert result['business_library_history'][-1]['snapshot']['media']['slot']==[str(p)]
    assert result['business_review_history'][0]['confirmed_at']
    assert result['business_review_history'][0]['evidence']['files'][0]['sha256']
    p.write_bytes(b'changed')
    with pytest.raises(ValueError,match='变更'):confirm_business(s,result,args,root=tmp_path,draft=draft,library=lib)


def test_original_form_requires_real_nonempty_locator_history_kept(tmp_path):
    s,state,draft,lib=context(tmp_path);doc=next(d for d in s.documents if d.role!='tech_spec')
    args=dict(item_key='P/original_form',decision='original_form',document_id=doc.document_id,locator='invalid',actor='人',basis='核对原文')
    with pytest.raises(ValueError,match='定位'):confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    args['locator']=doc.blocks[0].locator
    first=confirm_business(s,state,args,root=tmp_path,draft=draft,library=lib)
    args['basis']='重新核对'
    second=confirm_business(s,first,args,root=tmp_path,draft=draft,library=lib)
    assert len(second['business_review_history'])==2
    assert second['business_review_history'][1]['prior']==first['business_review_history'][0]
    assert not business_catalog(s,second,draft,lib)['complete']
    assert second['business_review_history'][-1]['status']=='reviewed_pending_regeneration'
