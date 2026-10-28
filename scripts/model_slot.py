"""Release the other GPU chat model before serial graph/interactive work."""
import argparse
import time
import httpx


def main(phase):
    with httpx.Client(base_url='http://127.0.0.1:11434',trust_env=False,timeout=120) as client:
        loaded=client.get('/api/ps').raise_for_status().json().get('models',[])
        targets=[row['name'] for row in loaded if (phase=='graph' and row['name']=='qwen3:8b')
            or (phase=='interactive' and row['name'].startswith('icp-qwen3-graph-'))]
        for name in targets:
            client.post('/api/generate',json={'model':name,'keep_alive':0,'stream':False}).raise_for_status()
        if targets:
            deadline=time.monotonic()+30;stable=0
            while time.monotonic()<deadline:
                remaining={row['name'] for row in client.get('/api/ps').raise_for_status().json().get('models',[])}
                stable=stable+1 if not remaining.intersection(targets) else 0
                if stable>=4:break
                time.sleep(.25)
            else:raise RuntimeError('Other chat profile has not finished unloading')
    print('Model phase prepared: '+phase+'; unloaded other chat profiles='+str(len(targets))+'. Weights/configuration retained; embedding model untouched.')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['graph','interactive'])
    try:main(parser.parse_args().phase)
    except Exception as exc:print('Model phase preparation failed; error type='+type(exc).__name__);raise SystemExit(1)
