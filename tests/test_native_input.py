import json
from pathlib import Path
import sys
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.dify_native_diagnosis import build


@pytest.mark.parametrize('value',['画面卡住，怎么排查？',json.dumps({'query':'画面卡住，怎么排查？'}),json.dumps('画面卡住，怎么排查？')])
def test_native_diagnostic_string_interface_accepts_natural_question_or_query_json(value):
    namespace={};node=next(n for n in build()['workflow']['graph']['nodes'] if n['id']=='input')
    exec(node['data']['code'],namespace)
    result=namespace['main'](value)
    assert result['query']=='画面卡住，怎么排查？'
    assert json.loads(json.loads(result['body'])['input'])=={'query':result['query']}


@pytest.mark.parametrize('value',['', '[]', 'null', '123', '{}'])
def test_native_diagnostic_rejects_missing_or_nonobject_payload(value):
    namespace={};node=next(n for n in build()['workflow']['graph']['nodes'] if n['id']=='input')
    exec(node['data']['code'],namespace)
    with pytest.raises(ValueError):namespace['main'](value)
