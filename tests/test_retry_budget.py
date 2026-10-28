import asyncio
import httpx
import pytest
from icp import integrations as api


@pytest.mark.parametrize('budget,expected',[(1,1),(2,2)])
def test_transport_budget_counts_physical_requests(monkeypatch,budget,expected):
    calls=[]
    async def reject(self,*args,**kwargs):
        calls.append(1)
        raise httpx.ConnectError('private transport detail')
    monkeypatch.setattr(httpx.AsyncClient,'request',reject)
    async def run():
        token=api.TRANSPORT_ATTEMPTS.set(budget)
        try:
            with pytest.raises(api.IntegrationError) as error:
                await api.request_json('POST','http://unused.test')
            assert 'private transport' not in str(error.value)
        finally:api.TRANSPORT_ATTEMPTS.reset(token)
    asyncio.run(run())
    assert len(calls)==expected


def test_nested_action_cannot_expand_single_attempt_budget(monkeypatch):
    calls=[]
    async def reject(self,*args,**kwargs):
        calls.append(1)
        return httpx.Response(503,request=httpx.Request('POST','http://unused.test'))
    monkeypatch.setattr(httpx.AsyncClient,'request',reject)
    async def run():
        token=api.TRANSPORT_ATTEMPTS.set(1)
        try:
            with pytest.raises(api.IntegrationError):
                await api.request_json('POST','http://unused.test',attempts=2)
        finally:api.TRANSPORT_ATTEMPTS.reset(token)
    asyncio.run(run())
    assert len(calls)==1
