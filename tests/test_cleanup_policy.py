from types import SimpleNamespace as NS
import pytest
from video_translate.config import CleanupConfig
from video_translate.policy_client import PolicyError, resolve_cleanup_policy, cached_policy


def test_cleanup_uses_remote_and_preserves_scene_text(monkeypatch):
    import video_translate.policy_client as client
    seen=[]
    def remote(payload, **kwargs):
        seen.append(payload)
        return {'success':True, 'action':'cleanup_policy', 'tracks':[{'track_id':'1',
            'kind':'scene_text','cleanup_eligible':False,'layout_eligible':False}],
            'subtitle_layout':{'version':1,'default':None,'segments':{}}}
    monkeypatch.setattr(client,'request_policy',remote)
    tracks=[{'track_id':'1','text':'label','start_ms':0,'end_ms':500,
             'bbox_norm':{'x':.2,'y':.2,'w':.3,'h':.1},'observations':[{}]}]
    result=resolve_cleanup_policy(job_id='test',video={'width':720,'height':1280,'duration_ms':2000},
        sample_count=8,tracks=tracks,segments=[NS(id=1,start_ms=0,end_ms=2000)],config=CleanupConfig())
    assert seen[0]['action']=='cleanup_policy'
    assert seen[0]['tracks'][0]['text']=='label'
    assert not tracks[0]['cleanup_eligible']
    assert result['default'] is None


def test_cleanup_network_failure_has_no_local_fallback(monkeypatch):
    import video_translate.policy_client as client
    def fail(*a,**k):raise PolicyError('POLICY_TIMEOUT')
    monkeypatch.setattr(client,'request_policy',fail)
    with pytest.raises(PolicyError,match='POLICY_TIMEOUT'):
        resolve_cleanup_policy(job_id='test',video={'width':720,'height':1280,'duration_ms':2000},
            sample_count=8,tracks=[],segments=[NS(id=1,start_ms=0,end_ms=2000)],config=CleanupConfig())


def test_policy_cache_reuses_plan_and_detects_changes(tmp_path):
    import json
    path=tmp_path/'cache.json';p={'action':'cleanup_policy','job_id':'x'}
    def remote(p):return {'success':True,'action':p['action'],'policy_version':'old'}
    first=cached_policy(p,path,remote)
    def forbidden(p):pytest.fail('repeated external call')
    assert cached_policy(p,path,forbidden)==first
    with pytest.raises(PolicyError,match='POLICY_CACHE_INPUT_CHANGED'):
        cached_policy({**p,'job_id':'changed'},path,forbidden)
    state=json.loads(path.read_text());state['output']['policy_version']='tampered';path.write_text(json.dumps(state))
    with pytest.raises(PolicyError,match='POLICY_CACHE_INTEGRITY_ERROR'):
        cached_policy(p,path,forbidden)


def test_timeout_resumes_original_task(monkeypatch,tmp_path):
    import video_translate.policy_client as client
    calls=[]
    monkeypatch.setattr(client,'_submit',lambda *a,**k:calls.append('submit') or 'task-1')
    def poll(task_id,**kw):
        calls.append(task_id)
        if calls.count(task_id)==1:raise PolicyError('POLICY_TIMEOUT')
        return {'success':True,'action':'alignment_policy'}
    monkeypatch.setattr(client,'_poll',poll)
    p={'action':'alignment_policy'};path=tmp_path/'cache.json'
    with pytest.raises(PolicyError,match='POLICY_TIMEOUT'):cached_policy(p,path)
    cached_policy(p,path)
    assert calls==['submit','task-1','task-1']
