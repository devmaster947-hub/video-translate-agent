from types import SimpleNamespace as NS
import pytest
from video_translate.network import request, ProviderError


def test_retries_are_bounded_and_auth_is_not_printed(monkeypatch):
    calls=[]
    monkeypatch.setattr("video_translate.network.time.sleep",lambda seconds: None)
    class Session:
        def request(self,*args,**kwargs):
            calls.append(kwargs)
            return NS(status_code=503)
    with pytest.raises(ProviderError,match="REMOTE_HTTP_503"):
        request("GET","https://example.invalid",session=Session(),headers={"Authorization":"fixture-key"})
    assert len(calls)==3 and all(c["verify"] and not c["allow_redirects"] for c in calls)


def test_auth_failure_not_retried():
    calls=[]
    class Session:
        def request(self,*args,**kwargs):
            calls.append(1)
            return NS(status_code=401)
    with pytest.raises(ProviderError,match="REMOTE_HTTP_401"):
        request("GET","https://example.invalid",session=Session())
    assert len(calls)==1
