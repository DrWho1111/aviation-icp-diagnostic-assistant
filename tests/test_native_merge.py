import json
from scripts.dify_native_diagnosis import build


def merge(bodies):
    node=next(n for n in build()['workflow']['graph']['nodes'] if n['id']=='merge')
    scope={};exec(node['data']['code'],scope)
    return json.loads(scope['main'](json.dumps({'id':'FUNC-410'}),'current',*bodies)['result'])


def body(status='ok',version='current',eid='evidence'):
    return json.dumps({'result':json.dumps({'status':status,'data':[],'evidence':[{'id':eid}],
        'corpus_version':version,'missing_fields':[],'coverage':{},'error':None})})


def test_invalid_json_branch_preserves_other_three_evidence_results():
    result=merge(['not JSON',body(eid='b'),body(eid='c'),body(eid='d')])
    assert result['status']=='error' and result['error']=='FTA_Query'
    assert {e['id'] for e in result['evidence']}=={'b','c','d'}
    assert len(result['data']['branches'])==4


def test_old_branch_version_is_not_merged_into_current_evidence():
    result=merge([body(version='old',eid='old-evidence'),body(),body(),body()])
    assert result['status']=='error' and result['corpus_version']=='current'
    assert 'old-evidence' not in json.dumps(result)


def test_branch_needing_parameters_cannot_report_complete_success():
    result=merge([body('needs_input'),body(),body(),body()])
    assert result['status']=='needs_input' and result['coverage']['FTA_Query']=='needs_input'
