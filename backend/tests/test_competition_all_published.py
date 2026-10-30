"""Real HTTP/SQL feed tests for dynamic publication access; synthetic credentials only."""
from dataclasses import replace
from test_competition_usage import configured, analytics_setup, feed, HEADERS, URL, PUBLIC, EXTERNAL, EMPTY  # noqa: F401

FLAG='COMPETITION_USAGE_ALL_PUBLISHED_ENABLED'

def enable(monkeypatch):
    monkeypatch.setenv(FLAG,'true')
    monkeypatch.delenv('COMPETITION_USAGE_AGENTS_JSON',raising=False)

def test_all_mode_still_requires_key(configured,monkeypatch):
    client,_,_=configured
    enable(monkeypatch)
    for headers in ({},{'Authorization':'Bearer wrong'}):
        assert client.get(URL,params={'name':PUBLIC.name},headers=headers).status_code==401
    monkeypatch.delenv('COMPETITION_USAGE_API_KEY')
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==503

def test_new_publication_appears_and_withdrawal_precedes_cache(configured,monkeypatch):
    client,_,_=configured
    enable(monkeypatch)
    roster=[PUBLIC,EXTERNAL]
    monkeypatch.setattr(feed.analytics,'_published_catalog',lambda:list(roster))
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==200
    assert client.get(URL,params={'name':EXTERNAL.name},headers=HEADERS).status_code==200
    assert client.get(URL,params={'name':EMPTY.name},headers=HEADERS).status_code==404
    roster.append(EMPTY)
    assert client.get(URL,params={'name':EMPTY.name},headers=HEADERS).status_code==200
    roster.remove(PUBLIC)
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==404
    assert client.get(URL,params={'name':'unpublished-personal-agent'},headers=HEADERS).status_code==404

def test_duplicate_names_fail_and_new_publisher_does_not_reuse_old_cache(configured,monkeypatch):
    client,_,_=configured
    enable(monkeypatch)
    roster=[PUBLIC]
    monkeypatch.setattr(feed.analytics,'_published_catalog',lambda:list(roster))
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==200
    other=replace(PUBLIC,owner_id='different-publisher')
    roster.append(other)
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==409
    roster.remove(PUBLIC)
    result=client.get(URL,params={'name':PUBLIC.name},headers=HEADERS)
    assert result.status_code==200 and result.json()['total_clicks']==0
    assert len(feed._cache)==2

def test_default_mode_retains_explicit_roster(configured,monkeypatch):
    client,_,_=configured
    monkeypatch.delenv(FLAG,raising=False)
    assert client.get(URL,params={'name':'制度助手'},headers=HEADERS).status_code==200
    assert client.get(URL,params={'name':PUBLIC.name},headers=HEADERS).status_code==404
