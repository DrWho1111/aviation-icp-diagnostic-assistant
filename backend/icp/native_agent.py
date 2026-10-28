"""Bridge application runs to a Dify-owned loop without exposing platform form tokens."""
import json
import os
from pathlib import Path
import uuid
import time
import httpx
from .integrations import IntegrationError
from .native_diagnosis import validate_result


def enabled():
    path=os.getenv('DIFY_NATIVE_MAIN_KEYS_FILE','')
    return bool(path and Path(path).is_file())


def partial_run_has_complete_result(run):
    """A recovered platform exception is acceptable only after logical completion."""
    result=run.get('result') or {};trace=run.get('trace',[])
    if (run.get('status')!='completed' or result.get('status') not in {'ok','not_found','not_covered'}
        or result.get('corpus_version')!=run.get('corpus_version') or not trace
        or trace[-1].get('tool')!='End' or trace[-1].get('status')!='ok'
        or any(t.get('status')=='error' for t in trace) or not any(t.get('tool')!='End' for t in trace)):
        return False
    data=result.get('data')
    if isinstance(data,dict) and 'branches' in data:
        branches=data['branches']
        if (not isinstance(branches,dict) or set(branches)!={'FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query'}
            or any(v.get('status') not in {'ok','not_found','not_covered'} for v in branches.values())):
            return False
    return True


async def execute(engine,run,confirmed=False):
    started=time.perf_counter();node_timings=[]
    mapping=json.loads(Path(os.environ['DIFY_NATIVE_MAIN_KEYS_FILE']).read_text(encoding='utf-8'))
    base=os.getenv('DIFY_BASE_URL','http://127.0.0.1:8081/v1').rstrip('/')
    private=engine.store.workflow(run['id']);root=run['id'];resume=False
    if private and private.get('kind')=='native_main':root=private.get('root_id',root);resume=bool(private.get('workflow_id'))
    elif run.get('context_source_run_id'):
        prior=engine.store.workflow(run['context_source_run_id'])
        if prior and prior.get('kind')=='native_main' and prior.get('purpose')=='input' and prior.get('phase')=='paused':
            private=dict(prior);root=private['root_id'];resume=True
    if private:
        if private.get('user')!=run['session_id'] or private.get('corpus_version')!=run['corpus_version']:
            raise IntegrationError('原生任务的会话或知识版本不匹配')
    else:private={'kind':'native_main','root_id':root,'phase':'starting','user':run['session_id'],'corpus_version':run['corpus_version']}
    private['active_run_id']=run['id']
    def save():
        engine.store.save_workflow(root,private)
        if root!=run['id']:engine.store.save_workflow(run['id'],private)
    save()
    async with httpx.AsyncClient(base_url=base,headers={'Authorization':'Bearer '+mapping['api_key']},timeout=180,trust_env=False) as client:
        try:
            if resume:
                if private['phase']=='paused':
                    if private['purpose']=='function':
                        fid=run.get('selected_function_id')
                        if not confirmed or fid not in private.get('candidate_ids',[]):raise IntegrationError('尚未完成有效的候选功能确认')
                        action='approve';inputs={'function_id':fid}
                    else:action='supply';inputs={'additional_input':run['question']}
                    response=await client.post('/form/human_input/'+private['form_token'],json={'action':action,'inputs':inputs,'user':run['session_id']})
                    if response.status_code not in {200,412}:response.raise_for_status()
                    if response.status_code==412 and response.json().get('code')!='human_input_form_submitted':raise IntegrationError('确认或补参表单已失效，请重新提问')
                    private.update(phase='resuming',submitted_token=private['form_token']);save()
                method,path,kwargs='GET','/workflow/'+private['workflow_id']+'/events',{'params':{'user':run['session_id'],'include_state_snapshot':'true','continue_on_pause':'true'}}
            else:
                method,path,kwargs='POST','/workflows/run',{'json':{'inputs':{'input':json.dumps({'root_run_id':root})},'response_mode':'streaming','user':run['session_id']}}
            async with client.stream(method,path,**kwargs) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line.startswith('data: '):continue
                    event=json.loads(line[6:]);name=event.get('event');data=event.get('data',{})
                    rid=event.get('workflow_run_id')
                    if rid:private['workflow_id']=rid;private['phase']='running';save()
                    if name=='node_finished' and isinstance(data.get('elapsed_time'),(int,float)) and len(node_timings)<250:
                        node_timings.append({'node_id':data.get('node_id'),'node_type':data.get('node_type'),
                            'elapsed_ms':round(data['elapsed_time']*1000,3),'status':data.get('status')})
                    if name=='human_input_required':
                        token=data.get('form_token') or event.get('form_token')
                        if token==private.get('submitted_token'):continue
                        definition=(await client.get('/form/human_input/'+token)).raise_for_status().json()
                        fields={v['output_variable_name'] for v in definition.get('inputs',[])}
                        run.update(engine.store.get(run['id']))
                        if 'function_id' in fields:
                            text=definition['form_content'];located=json.JSONDecoder().raw_decode(text[text.find('{'):])[0]
                            validate_result(engine.tools.kb,located)
                            if len(located.get('data',[]))!=3:raise IntegrationError('未得到三个可核对的功能候选')
                            run.update(status='needs_confirmation',candidates=located['data'],result=located,
                                answer='请先确认本次异常对应的功能，再执行四路检索。',planner_mode='dify')
                            run['evidence']=list({e['id']:e for e in run['evidence']+located['evidence']}.values())
                            run['trace'].append({'id':uuid.uuid4().hex,'tool':'Fault_Reasoning_Engine','params':{'query':run['question']},
                                'status':'needs_confirmation','backend':'dify','elapsed_ms':0,'cached':False,
                                'summary':'原生工作流功能定位后等待确认','observation':{**located,'status':'needs_confirmation'}})
                            private.update(purpose='function',candidate_ids=[r['id'] for r in located['data']])
                        elif 'additional_input' in fields:
                            run.update(status='needs_input',answer=definition['form_content'].split('{{')[0].strip(),planner_mode='dify')
                            private.update(purpose='input')
                        else:raise IntegrationError('无法识别原生表单字段')
                        private.update(phase='paused',form_token=token);save();engine.store.save(run);return
                    if name in {'workflow_finished','error'}:
                        run.update(engine.store.get(run['id']))
                        if name=='error' or data.get('status') not in {'succeeded','partial-succeeded'}:
                            raise IntegrationError('原生循环执行失败，已有工具结果已保留')
                        if data.get('status')=='partial-succeeded':
                            if not partial_run_has_complete_result(run):
                                raise IntegrationError('平台存在未完成的逻辑结果，不能报告完整诊断成功')
                            run['platform_note']='平台记录含已恢复异常；已核对逻辑结果完整、无失败分支并正常结束。'
                        if run['status'] in {'queued','running'}:
                            run.update(status='needs_input',answer='已达到原生循环执行边界，请缩小问题范围。')
                        private.update(phase='completed');private.pop('form_token',None);private.pop('submitted_token',None);save()
                        run['orchestration']='native_dify_loop';engine.store.save(run);return
            raise IntegrationError('原生工作流事件连接结束，尚未取得完成或暂停状态')
        except (httpx.HTTPError,KeyError,ValueError,TypeError):
            raise IntegrationError('原生循环通信或结果校验失败，私有工作流状态已保存') from None
        finally:
            saved=engine.store.get(run['id'])
            if saved:
                saved.setdefault('measurements',[]).append({'phase':'native_resume' if resume else 'native_start',
                    'active_wall_ms':round((time.perf_counter()-started)*1000,3),'nodes':node_timings,
                    'human_wait_excluded':True,'note':'overlapping branch node durations must not be summed as elapsed diagnosis time'})
                engine.store.save(saved)
