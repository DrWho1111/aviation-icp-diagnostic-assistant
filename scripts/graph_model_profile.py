"""Pin a no-thinking graph-only Qwen template when provider flags are not forwarded.

The main qwen3:8b model is unchanged. Keep an immutable old graph alias for rollback.
"""
from datetime import datetime
import hashlib
import json
import re
import sys
from pathlib import Path
import httpx

ROOT=Path(__file__).resolve().parents[1]


def no_thinking_template(template):
    substitutions={
        '{{- if and $.IsThinkSet (eq $i $lastUserIdx) }}':'{{- if eq $i $lastUserIdx }}',
        '{{- if $.Think -}}':'{{- if false -}}',
    }
    for source,target in substitutions.items():
        if template.count(source)!=1:raise ValueError('Qwen template differs; review before applying a graph profile')
        template=template.replace(source,target)
    return template


def apply_profile():
    # Provider registration can itself probe/load qwen3 after the caller's
    # phase switch. Release both chat configurations immediately before
    # creating/probing the graph alias; CPU embedding remains resident.
    sys.path.insert(0,str(ROOT))
    from scripts.model_slot import main as prepare_slot
    prepare_slot('interactive');prepare_slot('graph')
    backup='icp-qwen3-graph-thinking-baseline:latest'
    with httpx.Client(base_url='http://127.0.0.1:11434',trust_env=False,timeout=180) as client:
        def request(path,payload):return client.post(path,json=payload).raise_for_status().json()
        base=request('/api/show',{'model':'qwen3:8b'});template=no_thinking_template(base['template'])
        digest=hashlib.sha256(template.encode()).hexdigest()
        model='icp-qwen3-graph-'+digest[:8]+':latest'
        names={m['name'] for m in client.get('/api/tags').raise_for_status().json()['models']}
        if model in names and backup not in names:
            response=client.post('/api/copy',json={'source':model,'destination':backup});response.raise_for_status()
        created=request('/api/create',{'model':model,'from':'qwen3:8b','template':template,
            'parameters':{'num_ctx':8192,'num_predict':4096,'num_gpu':36,'num_batch':128,'temperature':0},'stream':False})
        if created.get('status')!='success':raise RuntimeError('Graph-only profile creation failed')
        after=request('/api/show',{'model':model})
        # Deliberately omit think: RAGFlow's provider path may omit that flag.
        probe=request('/api/chat',{'model':model,'messages':[{'role':'user','content':'只输出一个JSON对象，字段status的值是ok。'}],
            'stream':False,'options':{'num_predict':100,'temperature':0}})
        text=probe.get('message',{}).get('content','');thinking=probe.get('message',{}).get('thinking','')
        try:parsed=json.loads(re.sub(r'^```(?:json)?\s*|\s*```$','',text.strip()))
        except ValueError:parsed={}
        passed=after['template']==template and not thinking and '<think>' not in text and parsed.get('status')=='ok'
        unchanged=request('/api/show',{'model':'qwen3:8b'})
        passed=passed and unchanged['template']==base['template'] and unchanged['parameters']==base['parameters']
    report={'date':datetime.now().isoformat(),'name':model,'base_model':'qwen3:8b','backup_alias':backup,'num_ctx':8192,
        'num_predict':4096,'num_gpu':36,'num_batch':128,'temperature':0,'weights_reused':True,'main_model_unchanged':True,
        'template_sha256':digest,'model_name_contains_template_digest':True,'no_thinking_without_request_flag':passed,
        'probe_eval_tokens':probe.get('eval_count'),'probe_seconds':probe.get('total_duration',0)/1e9,'passed':passed,
        'probe_message_characters':len(text),'probe_thinking_characters':len(thinking),
        'scope':'graph alias template/readback and one actual response; extraction quality audited separately',
        'reason':'Provider-specific thinking controls must reach the native Ollama request; graph alias enforces no_think independently.'}
    (ROOT/'outputs/graph-model-profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'passed':passed,'main_model_unchanged':True,'probe_eval_tokens':report['probe_eval_tokens']}),flush=True)
    if not passed:raise RuntimeError('Graph no-thinking probe failed; rollback alias retained')
    return report


if __name__=='__main__':
    try:apply_profile()
    except Exception as exc:print('Graph profile stopped; error type='+type(exc).__name__);raise SystemExit(1)
