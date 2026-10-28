"""Set a reversible GPU-layer cap on the local Qwen tag, reusing unchanged weights."""
import argparse
from datetime import datetime
import json
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

ROOT=Path(__file__).resolve().parents[1]


def main(layers):
    if not 0<=layers<=36:raise ValueError('GPU layer cap must be between 0 and 36')
    model='qwen3:8b';backup='icp-qwen3-original:8b'
    with httpx.Client(base_url='http://127.0.0.1:11434',timeout=120,trust_env=False) as client:
        def request(path,body):
            response=client.post(path,json=body)
            if not response.is_success:raise RuntimeError('Local model profile API rejected the request')
            return response.json() if response.content else {}
        names={m['name'] for m in client.get('/api/tags').raise_for_status().json()['models']}
        if model not in names:raise RuntimeError('Download qwen3:8b first')
        if backup not in names:request('/api/copy',{'source':model,'destination':backup})
        before=request('/api/show',{'model':backup})
        directory=ROOT/'.models/resource-profile';directory.mkdir(exist_ok=True,parents=True)
        (directory/'original-qwen3.json').write_text(json.dumps(before,ensure_ascii=False,indent=2),encoding='utf-8')
        request('/api/generate',{'model':model,'keep_alive':0,'stream':False})
        value=request('/api/create',{'model':model,'from':backup,'stream':False,
                    'parameters':{'num_ctx':8192,'num_gpu':layers,'num_batch':128}})
        if value.get('status')!='success':raise RuntimeError('Local model profile did not finish')
        after=request('/api/show',{'model':model})
        parameters=after.get('parameters','')
        if f'num_gpu' not in parameters or str(layers) not in parameters:raise RuntimeError('Model resource parameters were not retained')
        if 'bge-m3:latest' in names:
            embedding_backup='icp-bge-m3-original:latest'
            if embedding_backup not in names:request('/api/copy',{'source':'bge-m3:latest','destination':embedding_backup})
            embedded=request('/api/create',{'model':'bge-m3:latest','from':embedding_backup,'stream':False,'parameters':{'num_gpu':0,'num_batch':8192,'num_ctx':8192}})
            if embedded.get('status')!='success':raise RuntimeError('Embedding resource profile did not finish')
    report={'date':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(),'passed':True,'model':model,
            'base_snapshot':backup,'gpu_layers':layers,'num_ctx':8192,'num_batch':128,
            'weights_reused':True,'training_performed':False,'embedding_gpu_layers':0,
            'embedding_num_batch':8192,'embedding_num_ctx':8192,
            'scope':'运行配置回读检查；实际模型与平台问答另行回归；不能保证其他程序任意占满显存时仍可运行',
            'reference':'https://github.com/ollama/ollama/blob/main/api/types.go'}
    (ROOT/'outputs/model-resource-profile.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['passed','model','gpu_layers','num_ctx','num_batch','weights_reused']}))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--gpu-layers',type=int,default=24)
    try:main(parser.parse_args().gpu_layers)
    except Exception as exc:
        print('Model resource configuration stopped; error type='+type(exc).__name__)
        raise SystemExit(1)
