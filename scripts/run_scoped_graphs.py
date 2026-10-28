"""Compile source-bounded graph units one at a time and retain actual model outputs."""
import json
from pathlib import Path
import re
import time
import httpx
from dotenv import dotenv_values

ROOT=Path(__file__).resolve().parents[1]


def main():
    kb=json.loads((ROOT/'data/processed/knowledge.json').read_text(encoding='utf-8'));version=kb['version']
    path=ROOT/('.secrets/scoped-graphs-'+version+'.json');state=json.loads(path.read_text(encoding='utf-8'))
    env=dotenv_values(ROOT/'.env');out=ROOT/'outputs/scoped-graphs';out.mkdir(exist_ok=True)
    report={'date':'2026-10-24','corpus_version':version,'units':[],'scope':'source-derived preprocessing and native RAGFlow compilation; attributes/relationships require source reconciliation'}
    def save():
        report['summary']={'total':len(state['documents']),'executed':len(report['units']),'done':sum(u['status']=='DONE' for u in report['units']),
            'primary_id_present':sum(u.get('primary_id_present',False) for u in report['units'])}
        (ROOT/'outputs/scoped-graph-compilation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    with httpx.Client(base_url=env['RAGFLOW_BASE_URL']+'/api/v1',headers={'Authorization':'Bearer '+env['RAGFLOW_API_KEY']},trust_env=False,timeout=60) as client:
        def request(method,route,**kwargs):
            value=client.request(method,route,**kwargs).raise_for_status().json()
            if value.get('code')!=0:raise RuntimeError('Scoped graph service rejected request')
            return value.get('data')
        profile=json.loads((ROOT/'outputs/graph-model-profile.json').read_text(encoding='utf-8'))
        desired_model=profile['name']+'@icp-local@Ollama'
        group_configs={}
        for group,item in state['groups'].items():
            data=request('GET','/datasets',params={'id':item['dataset_id']})
            dataset=data[0] if isinstance(data,list) else data
            config={**dataset.get('parser_config',{}),'llm_id':desired_model}
            if dataset.get('parser_config',{}).get('llm_id')!=desired_model:
                request('PUT','/datasets/'+item['dataset_id'],json={'parser_config':config})
            group_configs[group]=config
        path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        for rid,item in state['documents'].items():
            ds=state['groups'][item['group']]['dataset_id'];did=item['document_id'];start=time.monotonic()
            docs=request('GET',f'/datasets/{ds}/documents',params={'id':did,'page_size':100})['docs']
            doc=next(d for d in docs if d['id']==did)
            if item.get('execution_model_name')!=profile['name'] and item.get('queued'):item['needs_recompile']=True
            prior_deadline=time.monotonic()+180
            while item.get('needs_recompile') and str(doc['run']) in {'RUNNING','1','SCHEDULE','5'} and time.monotonic()<prior_deadline:
                time.sleep(2);docs=request('GET',f'/datasets/{ds}/documents',params={'id':did,'page_size':100})['docs'];doc=next(d for d in docs if d['id']==did)
            if item.get('needs_recompile') and str(doc['run']) in {'RUNNING','1','SCHEDULE','5'}:
                report['units'].append({'record_id':rid,'group':item['group'],'status':'PRIOR_TASK_STILL_RUNNING','seconds':round(time.monotonic()-start,2)})
                save();print(json.dumps(report['units'][-1]),flush=True);continue
            if item.get('needs_recompile') or str(doc['run']) not in {'DONE','3','RUNNING','1','SCHEDULE','5'}:
                config={**group_configs[item['group']],**doc.get('parser_config',{}),'llm_id':desired_model}
                request('PATCH',f'/datasets/{ds}/documents/{did}',json={'parser_config':config})
                request('POST',f'/datasets/{ds}/chunks',json={'document_ids':[did]});item['queued']=True
                item['execution_model_profile']=profile['template_sha256'];item['execution_model_name']=profile['name']
                doc['run']='SCHEDULE'
                path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            deadline=time.monotonic()+900
            while str(doc['run']) not in {'DONE','3','FAIL','4'} and time.monotonic()<deadline:
                time.sleep(2)
                docs=request('GET',f'/datasets/{ds}/documents',params={'id':did,'page_size':100})['docs'];doc=next(d for d in docs if d['id']==did)
            row={'record_id':rid,'group':item['group'],'status':'DONE' if str(doc['run']) in {'DONE','3'} else 'FAILED_OR_TIMEOUT','seconds':round(time.monotonic()-start,2)}
            row['model_template_sha256']=item.get('execution_model_profile')
            row['configured_model_name']=item.get('execution_model_name')
            if row['status']=='DONE':
                graph=request('GET',f'/datasets/{ds}/documents/{did}/structure/graph')
                (out/(rid+'.json')).write_text(json.dumps(graph,ensure_ascii=False,indent=2),encoding='utf-8')
                entities=[e for t in graph.get('templates',[]) for e in t.get('entities',[])]
                all_text=json.dumps(entities,ensure_ascii=False).translate(str.maketrans({'‑':'-','–':'-','—':'-'}))
                row.update(entities=len(entities),relations=graph.get('total_relations',0),primary_id_present=rid in all_text)
                item['needs_recompile']=False;item['compiled_template_sha256']=state['groups'][item['group']].get('template_sha256')
                path.write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
            report['units'].append(row);save();print(json.dumps(row),flush=True)
    report['complete']=len(report['units'])==len(state['documents']);save()


if __name__=='__main__':
    try:main()
    except Exception as exc:print('Scoped compilation stopped; checkpoint retained; error type='+type(exc).__name__);raise SystemExit(1)
