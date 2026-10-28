import asyncio
import copy
import json

import pytest

from icp.integrations import IntegrationError,dify_tool
from icp.native_agent import partial_run_has_complete_result


def test_direct_locator_and_native_locator_share_the_one_retry_budget():
    from scripts.export_workflows import workflow
    import yaml
    from pathlib import Path
    generated=next(n['data']['retry_config'] for n in workflow('Function_Locator')['workflow']['graph']['nodes'] if n['data']['type']=='http-request')
    exported=yaml.safe_load((Path(__file__).resolve().parents[1]/'deploy/dify-tools/Function_Locator.yml').read_text(encoding='utf-8'))
    actual=next(n['data']['retry_config'] for n in exported['workflow']['graph']['nodes'] if n['data']['type']=='http-request')
    assert actual==generated=={'retry_enabled':True,'max_retries':1,'retry_interval':200}
    assert next(n['data']['retry_config'] for n in workflow('End')['workflow']['graph']['nodes'] if n['data']['type']=='http-request')['retry_enabled'] is False


@pytest.mark.parametrize('status',['ok','not_found','not_covered','needs_input'])
def test_recovered_dify_exception_requires_a_valid_nonerror_tool_result(monkeypatch,tmp_path,status):
    mapping=tmp_path/'tools.json';mapping.write_text(json.dumps({'Hybrid_Search':'test-only-key'}))
    monkeypatch.setenv('DIFY_TOOL_KEYS_FILE',str(mapping))
    payload={'status':status,'data':[],'evidence':[],'missing_fields':[],'coverage':{},'error':None,'corpus_version':'kb-test'}
    async def request(*args,**kwargs):return {'data':{'status':'partial-succeeded','outputs':{'result':json.dumps(payload)}}}
    monkeypatch.setattr('icp.integrations.request_json',request)
    result=asyncio.run(dify_tool('Hybrid_Search',{'query':'test'},'test-user'))
    assert result['status']==status and result['coverage']['recovered_platform_exception'] is True


def test_partial_platform_status_does_not_hide_an_error_payload(monkeypatch,tmp_path):
    mapping=tmp_path/'tools.json';mapping.write_text(json.dumps({'Hybrid_Search':'test-only-key'}))
    monkeypatch.setenv('DIFY_TOOL_KEYS_FILE',str(mapping))
    payload={'status':'error','data':[],'evidence':[],'missing_fields':[],'coverage':{},'error':'failed','corpus_version':'kb-test'}
    async def request(*args,**kwargs):return {'data':{'status':'partial-succeeded','outputs':{'result':json.dumps(payload)}}}
    monkeypatch.setattr('icp.integrations.request_json',request)
    with pytest.raises(IntegrationError):asyncio.run(dify_tool('Hybrid_Search',{'query':'test'},'test-user'))


def finished(diagnosis=False):
    run={'status':'completed','corpus_version':'kb-test','result':{'status':'ok','corpus_version':'kb-test','data':[]},
         'trace':[{'tool':'Hybrid_Search','status':'ok'},{'tool':'End','status':'ok'}]}
    if diagnosis:run['result']['data']={'branches':{name:{'status':'ok'} for name in ['FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query']}}
    return run


@pytest.mark.parametrize('diagnosis',[False,True])
def test_recovered_completion_requires_normal_end_and_all_logical_results(diagnosis):
    assert partial_run_has_complete_result(finished(diagnosis))


@pytest.mark.parametrize('failure',['run_error','missing_end','logical_error','different_version','failed_branch','missing_branch'])
def test_partial_workflow_never_promotes_incomplete_diagnosis_to_success(failure):
    run=copy.deepcopy(finished(True))
    if failure=='run_error':run['status']='error'
    if failure=='missing_end':run['trace'].pop()
    if failure=='logical_error':run['trace'][0]['status']='error'
    if failure=='different_version':run['result']['corpus_version']='old'
    if failure=='failed_branch':run['result']['data']['branches']['FTA_Query']['status']='error'
    if failure=='missing_branch':del run['result']['data']['branches']['FTA_Query']
    assert not partial_run_has_complete_result(run)
