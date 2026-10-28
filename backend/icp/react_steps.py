"""Single-round operations invoked by a Dify-owned loop; no loop lives here."""
import json
import re
import uuid
from . import planner
from .query_intent import constrain_action
from .parameters import mission_hours
from .integrations import IntegrationError
from .native_diagnosis import validate_result


def current_run(engine,root):
    private=engine.store.workflow(root) or {}
    run=engine.store.get(private.get('active_run_id',root))
    if not run or run['corpus_version']!=engine.tools.kb.version:raise IntegrationError('原生循环任务或知识版本不匹配')
    return run


async def operate(engine,operation,payload):
    root=payload['root_run_id'];run=current_run(engine,root);question=run.get('effective_question',run['question'])
    if operation=='plan':
        if run['status'] in {'completed','error'}:return {'kind':'done','root_run_id':root}
        if run.get('native_input_request'):
            return {'kind':'input','root_run_id':root,'note':run['native_input_request']}
        pending=run.get('native_pending_action')
        if pending:
            return {'kind':'diagnosis' if pending['tool']=='Fault_Reasoning_Engine' else 'execute','root_run_id':root,
                'action_id':pending.get('id',''),'diagnosis_input':json.dumps({'query':pending['params']['query']},ensure_ascii=False)}
        observations=run.get('observations',[])
        if len(run['trace'])>=11:
            run.update(status='needs_input',answer='已达到12步执行限制，请缩小问题范围。');engine.store.save(run)
            return {'kind':'done','root_run_id':root}
        action=constrain_action(question,await planner.choose(question,observations,run['session_id']),engine.tools.kb)
        if action['tool']=='End' and not observations:
            action=constrain_action(question,{'tool':run.get('route','Hybrid_Search'),'query':question,
                'corpus':'synthetic','missing_fields':[],'summary':'当前无证据，查询相关资料'},engine.tools.kb)
        run['planner_mode']='dify'
        tool=action['tool'];params={'query':action['query']}
        if tool=='Hybrid_Search':params['corpus']=action['corpus']
        missing=action['missing_fields']
        if tool=='RBD_Query' and re.search('计算|可靠度',params['query']):
            try:hours=mission_hours(params['query'])
            except (TypeError,ValueError,OverflowError):hours=None
            if hours is None:missing=['单个非负、有限的任务时间（小时、分钟或天；不接受范围）']
        if missing:
            # Publish the paused status only after Dify has persisted its form token.
            # Otherwise an immediate reply can arrive before there is a form to resume.
            run.update(status='running',answer='请补充：'+'、'.join(missing));engine.store.save(run)
            return {'kind':'input','root_run_id':root,'note':run['answer']}
        if tool=='End' or any(t['tool']==tool and t['params']==params for t in run['trace']):
            run.update(status='completed',answer='\n\n'.join(run.get('answer_parts',[])))
            if not run['answer']:
                run.update(status='needs_input',answer='当前没有足够证据，请补充明确对象或问题。')
            else:
                selected=observations[-1]['tool'] if observations else tool
                await engine.enrich_answer(run,selected,question)
                await engine.call(run,'End',{'query':run['answer']})
            engine.store.save(run);return {'kind':'done','root_run_id':root}
        run['native_pending_action']={'id':uuid.uuid4().hex,'tool':tool,'params':params};engine.store.save(run)
        return {'kind':'diagnosis' if tool=='Fault_Reasoning_Engine' else 'execute','root_run_id':root,
                'action_id':run['native_pending_action']['id'],
                'diagnosis_input':json.dumps({'query':params['query']},ensure_ascii=False)}
    if operation=='continue':
        # The application has already bound the new question to this paused workflow.
        run.pop('native_input_request',None)
        run.update(status='running');engine.store.save(run);return {'root_run_id':root,'done':'0'}
    action=run.get('native_pending_action')
    cached=run.get('native_completed_action')
    if cached and payload.get('action_id')==cached.get('id'):return cached['response']
    if not action:raise IntegrationError('不存在待执行的单工具动作')
    if payload.get('action_id') and payload['action_id']!=action.get('id'):raise IntegrationError('动作已失效，不能执行旧动作')
    tool=action['tool']
    if operation=='execute':
        if tool=='Fault_Reasoning_Engine':raise IntegrationError('诊断必须经过原生确认分支')
        result=await engine.call(run,tool,action['params'])
    elif operation=='observe':
        if tool!='Fault_Reasoning_Engine' or not run.get('selected_function_id'):raise IntegrationError('缺少已绑定的功能确认')
        result=json.loads(payload['result']);validate_result(engine.tools.kb,result)
        if result.get('data',{}).get('function',{}).get('id')!=run['selected_function_id']:raise IntegrationError('结果功能不匹配')
        run['evidence']=list({e['id']:e for e in run['evidence']+result['evidence']}.values())
        run['trace'].append({'id':uuid.uuid4().hex,'tool':tool,'params':action['params'],'status':result['status'],'backend':'dify',
            'elapsed_ms':0,'cached':False,'summary':'Dify原生确认后四路检索','observation':result,
            'branches':[{'tool':n,'status':v['status']} for n,v in result['data']['branches'].items()]})
    else:raise IntegrationError('不支持的原生循环操作')
    from .engine import format_records
    if tool=='Fault_Reasoning_Engine':
        function=engine.tools.kb.records[run['selected_function_id']]
        pieces=['已确认功能：'+function['name']+'（'+function['id']+'）','以下为资料支持的候选原因，尚未确认本次根因。']
        if result['status']!='ok':pieces.insert(0,'四路查询尚未全部完成，不能报告完整诊断成功。')
        for n,v in result['data']['branches'].items():pieces.append(n+'\n'+format_records(n,v,expand_fta=False))
        answer='\n\n'.join(pieces)
    else:answer=format_records(tool,result)
    # Retrieved excerpts are observations, never an interim final response.
    run.update(result=result,answer='正在核对检索证据并整理答复…' if tool=='Hybrid_Search' else answer,generation='template')
    run.setdefault('observations',[]).append({'tool':tool,'status':result['status'],'summary':answer[:3500]})
    run.setdefault('answer_parts',[]).append(answer)
    done=result['status']=='error'
    if result['status']=='needs_input':
        # The next native loop round creates an actual persisted Human Input form.
        # A tool discovering missing parameters must not end the workflow.
        run['native_input_request']=answer
        run['answer_parts'].pop()
    if done:run['status']='error'
    else:run['status']='running'
    response={'root_run_id':root,'done':'1' if done else '0'}
    run['native_completed_action']={'id':action.get('id'),'response':response}
    run.pop('native_pending_action',None)
    engine.store.save(run)
    return response
