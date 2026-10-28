"""Controlled faults: real adapters with deterministic transport, no live-platform claim."""
import asyncio
import httpx
import pytest
from icp.integrations import request_json,IntegrationError
from icp.timing import TIMINGS


@pytest.mark.parametrize('fault',['timeout','unavailable','json','oom'])
def test_controlled_provider_fault_is_bounded_and_does_not_expose_body(monkeypatch,fault):
    calls=[]
    async def fail(self,method,url,**kwargs):
        calls.append(1)
        request=httpx.Request(method,url)
        if fault=='timeout':raise httpx.ReadTimeout('PRIVATE-FAULT-DETAIL',request=request)
        if fault=='json':return httpx.Response(200,text='PRIVATE-FAULT-DETAIL {broken',request=request)
        status=503 if fault=='unavailable' else 500
        return httpx.Response(status,json={'error':'PRIVATE-FAULT-DETAIL CUDA out of memory'},request=request)
    monkeypatch.setattr(httpx.AsyncClient,'request',fail)
    async def run():
        samples=[];token=TIMINGS.set(samples)
        try:
            with pytest.raises(IntegrationError) as exc:
                await request_json('POST','http://unused.test/api/chat',key='PRIVATE-TEST-CREDENTIAL')
            assert 'PRIVATE-' not in str(exc.value)
            assert samples and all(x['category']=='local_model_service' for x in samples)
            assert 'PRIVATE-' not in str(samples) and 'unused.test' not in str(samples)
        finally:TIMINGS.reset(token)
    asyncio.run(run())
    assert len(calls)==(2 if fault in {'timeout','unavailable'} else 1)
