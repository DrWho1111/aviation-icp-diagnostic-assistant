import json
import httpx
import pytest
from scripts import model_slot


@pytest.mark.parametrize('phase,expected',[('graph',{'qwen3:8b'}),('interactive',{'icp-qwen3-graph-example:latest'})])
def test_phase_change_unloads_only_the_other_chat_model_and_waits(monkeypatch,phase,expected):
    loaded={'qwen3:8b','icp-qwen3-graph-example:latest','bge-m3:latest'};stopped=[];polls=[]
    original=httpx.Client
    def respond(request):
        if request.method=='GET' and request.url.path=='/api/ps':
            polls.append(1);return httpx.Response(200,json={'models':[{'name':name} for name in sorted(loaded)]})
        assert request.method=='POST' and request.url.path=='/api/generate'
        body=json.loads(request.content)
        assert body['keep_alive']==0 and not body.get('prompt')
        stopped.append(body['model']);loaded.remove(body['model'])
        return httpx.Response(200,json={'done':True})
    monkeypatch.setattr(model_slot.httpx,'Client',lambda **kwargs:original(transport=httpx.MockTransport(respond),**kwargs))
    monkeypatch.setattr(model_slot.time,'sleep',lambda _:None)
    model_slot.main(phase)
    assert set(stopped)==expected and 'bge-m3:latest' in loaded and len(polls)>=5
