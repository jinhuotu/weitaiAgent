from api.services.tenders.facts import dump_facts,load_facts,apply_correction
from api.services.tenders.id_card import parse_id_card_text,dump_id_meta,load_id_meta


def test_ocr_name_with_split_labels_is_persisted():
    card=parse_id_card_text('姓 名：阿依古丽·测试姓名\n性 别：女\n民族：汉')
    assert card['name']=='阿依古丽·测试姓名'
    assert load_id_meta(dump_id_meta(card))['name']==card['name']


def test_uncertain_json_survives_save_and_manual_correction():
    candidate={'name':'未知属性','value':'待核实内容','conf':0.3}
    bundle={'role':'other','fields':[candidate],
            'candidates':[candidate],'rawRecognition':{'fields':[candidate],'extra':'原始信息'}}
    saved=dump_facts(bundle)
    assert load_facts(saved)['fields']==[]
    corrected=apply_correction(saved,role='other',fields=[{'name':'确认字段','value':'确认值'}])
    restored=load_facts(corrected)
    assert restored['candidates']==[candidate]
    assert restored['rawRecognition']['extra']=='原始信息'
    assert restored['fields'][0]['value']=='确认值'
