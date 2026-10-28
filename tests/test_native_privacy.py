import json
import pytest
from icp.storage import Store
from icp.knowledge import Knowledge
from icp.native_diagnosis import validate_result
from icp.integrations import IntegrationError


def test_private_form_token_never_enters_public_conversation():
    store=Store('sqlite:///:memory:')
    store.ensure_conversation('conversation','browser-client','test')
    row={'id':'run1','status':'needs_confirmation','session_id':'conversation','created_at':1,'question':'test','evidence':[],'answer':'pending'}
    store.save(row,create=True)
    store.save_workflow('run1',{'form_token':'PRIVATE-TEST-FORM-TOKEN','workflow_id':'test-workflow'})
    assert store.workflow('run1')['form_token']=='PRIVATE-TEST-FORM-TOKEN'
    assert 'PRIVATE-TEST-FORM-TOKEN' not in json.dumps(store.get('run1'))
    assert 'PRIVATE-TEST-FORM-TOKEN' not in json.dumps(store.conversation('conversation','browser-client'))
    store.delete_conversations('browser-client','conversation')
    assert store.workflow('run1') is None


def test_native_results_reject_valid_id_with_modified_source_text():
    kb=Knowledge();e=next(iter(kb.data['evidence'].values()))
    validate_result(kb,{'corpus_version':kb.version,'evidence':[e]})
    with pytest.raises(IntegrationError):
        validate_result(kb,{'corpus_version':kb.version,'evidence':[{**e,'raw_text':'invented'}]})
    with pytest.raises(IntegrationError):
        validate_result(kb,{'corpus_version':'old','evidence':[e]})


def test_restart_keeps_native_workflow_identity_and_recovers_only_submitted_runs():
    store=Store('sqlite:///:memory:')
    for rid,status,phase in [('paused','needs_confirmation','paused'),('submitted','running','resuming'),('legacy','running',None)]:
        store.save({'id':rid,'status':status,'session_id':'test','created_at':1,'selected_function_id':'FUNC-410'},create=True)
        if phase:store.save_workflow(rid,{'phase':phase,'workflow_id':'same-'+rid})
    recovered=store.recover_interrupted()
    assert [r['id'] for r in recovered]==['submitted']
    assert store.workflow('submitted')['workflow_id']=='same-submitted'
    assert store.get('paused')['status']=='needs_confirmation'
    assert store.get('legacy')['status']=='error'
