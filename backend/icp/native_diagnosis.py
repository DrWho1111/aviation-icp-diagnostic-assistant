"""Native Dify diagnostic lifecycle. Private form tokens never enter public run snapshots."""
import asyncio
import json
import os
from pathlib import Path
import time
import uuid
import httpx
from .integrations import IntegrationError


def enabled():
    path=os.getenv('DIFY_NATIVE_DIAGNOSIS_KEYS_FILE','')
    return bool(path and Path(path).is_file())


def validate_result(kb,result):
    if (not isinstance(result,dict) or result.get('corpus_version')!=kb.version
            or not isinstance(result.get('evidence'),list)
            or any(not isinstance(e,dict) or e.get('id') not in kb.data['evidence'] or e!=kb.data['evidence'][e['id']] for e in result.get('evidence',[]))):
        raise IntegrationError('原生工作流的知识版本或来源不匹配')


async def execute(engine,run,confirmed=False):
    started=time.monotonic()
    mapping=json.loads(Path(os.environ['DIFY_NATIVE_DIAGNOSIS_KEYS_FILE']).read_text(encoding='utf-8'))
    base=os.getenv('DIFY_BASE_URL','http://127.0.0.1:8081/v1').rstrip('/')
    private=engine.store.workflow(run['id'])
    async with httpx.AsyncClient(base_url=base,headers={'Authorization':'Bearer '+mapping['api_key']},trust_env=False,timeout=180) as client:
        try:
            if not confirmed:
                if private:raise IntegrationError('此任务已有原生工作流，不能重复创建')
                async with client.stream('POST','/workflows/run',json={'inputs':{'input':json.dumps({'query':run.get('effective_question',run['question'])},ensure_ascii=False)},'response_mode':'streaming','user':run['session_id']}) as response:
                    response.raise_for_status();form=None;rid=None
                    async for line in response.aiter_lines():
                        if not line.startswith('data: '):continue
                        event=json.loads(line[6:]);rid=event.get('workflow_run_id') or rid
                        if event.get('event')=='human_input_required':form=event;break
                        if event.get('event') in {'workflow_finished','error','workflow_paused'}:break
                if not form:raise IntegrationError('原生功能定位未进入确认阶段')
                data=form.get('data',form);token=data.get('form_token') or form.get('form_token')
                definition=(await client.get('/form/human_input/'+token)).raise_for_status().json()
                content=definition.get('form_content','');start=content.find('{')
                located=json.JSONDecoder().raw_decode(content[start:])[0]
                validate_result(engine.tools.kb,located)
                if len(located.get('data',[]))!=3 or any(r['id'] not in engine.tools.kb.records for r in located['data']):
                    raise IntegrationError('功能候选格式不正确')
                engine.store.save_workflow(run['id'],{'workflow_id':rid,'form_token':token,'phase':'paused','user':run['session_id'],
                    'corpus_version':run['corpus_version'],'candidate_ids':[r['id'] for r in located['data']]})
                run.update(status='needs_confirmation',candidates=located['data'],result=located,
                    answer='请先确认本次异常对应的功能，再查询故障树、失效影响、可靠性结构和历史案例。')
                trace_result={**located,'status':'needs_confirmation'}
            else:
                if (not private or private['user']!=run['session_id'] or private['corpus_version']!=engine.tools.kb.version
                        or run['selected_function_id'] not in private['candidate_ids']):
                    raise IntegrationError('原生工作流确认绑定不匹配')
                if private['phase']=='paused':
                    response=await client.post('/form/human_input/'+private['form_token'],json={'action':'approve','inputs':{'function_id':run['selected_function_id']},'user':run['session_id']})
                    if response.status_code not in {200,412}:response.raise_for_status()
                    if response.status_code==412 and response.json().get('code')!='human_input_form_submitted':
                        raise IntegrationError('原生确认表单已失效，请重新定位功能')
                    private['phase']='resuming';engine.store.save_workflow(run['id'],private)
                deadline=time.monotonic()+180
                while True:
                    result=(await client.get('/workflows/run/'+private['workflow_id'])).raise_for_status().json()
                    if result.get('status') in {'succeeded','partial-succeeded','failed','stopped'}:break
                    if time.monotonic()>deadline:raise IntegrationError('原生诊断工作流仍未完成，已保留运行标识')
                    await asyncio.sleep(.5)
                if result.get('status') not in {'succeeded','partial-succeeded'}:raise IntegrationError('原生诊断工作流执行失败')
                trace_result=json.loads(result['outputs']['result']);validate_result(engine.tools.kb,trace_result)
                if trace_result.get('status')!='ok':
                    run.update(status='error',result=trace_result,error='原生诊断分支未全部完成',
                        answer='原生诊断分支未全部完成，不能报告诊断成功；已保留其他分支证据，请查看执行记录。')
                    run['evidence']=list({e['id']:e for e in run['evidence']+trace_result['evidence']}.values())
                    run['trace'].append({'id':uuid.uuid4().hex,'tool':'Fault_Reasoning_Engine','params':{'query':run['question']},
                        'status':'error','elapsed_ms':round((time.monotonic()-started)*1000,1),'cached':False,
                        'summary':'原生诊断分支失败，保留其他结果','backend':'dify','observation':trace_result,
                        'branches':[{'tool':n,'status':v['status']} for n,v in trace_result['data']['branches'].items()]})
                    private.update(phase='failed');private.pop('form_token',None);engine.store.save_workflow(run['id'],private)
                    engine.store.save(run);return
                from .engine import format_records
                branches=trace_result['data']['branches'];function=trace_result['data']['function']
                if result.get('status')=='partial-succeeded':
                    if (set(branches)!={'FTA_Query','FMECA_Query','RBD_Query','Fault_History_Query'}
                        or any(v.get('status') not in {'ok','not_found','not_covered'} for v in branches.values())):
                        raise IntegrationError('平台存在未完成分支，不能报告完整诊断成功')
                    run['platform_note']='平台记录含已恢复异常；四路逻辑结果已核对完整。'
                if function['id']!=run['selected_function_id']:raise IntegrationError('返回功能与确认不一致')
                pieces=[f"已确认功能：{function['name']}（{function['id']}）",'以下为合成资料支持的候选原因，尚未确认本次根因。']
                for name,label in [('FTA_Query','故障逻辑'),('FMECA_Query','失效影响与检测'),('RBD_Query','成功结构'),('Fault_History_Query','相似历史案例')]:
                    validate_result(engine.tools.kb,branches[name]);pieces.append(label+'\n'+format_records(name,branches[name],expand_fta=False))
                pieces.append('请核对完整HMC、发生阶段和现场观测；历史案例不能证明本次根因。')
                run.update(status='completed',result=trace_result,answer='\n\n'.join(pieces),generation='template')
                private.update(phase='completed');private.pop('form_token',None);engine.store.save_workflow(run['id'],private)
            trace={'id':uuid.uuid4().hex,'tool':'Fault_Reasoning_Engine','params':{'query':run['question']},'status':trace_result['status'],
                'elapsed_ms':round((time.monotonic()-started)*1000,1),'cached':False,'summary':'Dify原生定位与确认' if not confirmed else '恢复同一个Dify原生工作流并合并四路证据','backend':'dify',
                'observation':trace_result,'orchestration':'native_dify'}
            if confirmed:trace['branches']=[{'tool':n,'status':v['status']} for n,v in trace_result['data']['branches'].items()]
            run['trace'].append(trace);run['evidence']=list({e['id']:e for e in run['evidence']+trace_result['evidence']}.values())
            engine.store.save(run)
        except (httpx.HTTPError,ValueError,KeyError,TypeError):
            raise IntegrationError('原生诊断通信或结果校验失败；私有运行状态已保留') from None
