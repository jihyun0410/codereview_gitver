"""REST 클라이언트 검증 — 서버 없이 요청 경로·본문·오류 해석만 확인한다."""

from __future__ import annotations

import json

import httpx
import pytest

from codetest.api_client import AgentClient, ApiError

BASE = "http://proxy:80/agent/abc"


@pytest.fixture
def sent(monkeypatch):
    class Calls(list):
        pass

    calls = Calls()
    reply = {"status": 200, "json": {"ok": True}}

    def fake_request(method, url, headers=None, json=None, params=None, timeout=None):
        calls.append({"method": method, "url": url, "headers": headers, "json": json, "params": params})
        return httpx.Response(reply["status"], json=reply["json"], request=httpx.Request(method, url))

    monkeypatch.setattr(httpx, "request", fake_request)
    calls.reply = reply
    return calls


def test_register_posts_to_project_register(sent):
    AgentClient(BASE, "k").create_project("n", "https://g/x.git", "me", sources=[{"path": "a", "content": "b"}])
    call = sent[0]
    assert (call["method"], call["url"]) == ("POST", f"{BASE}/project/register")
    assert call["headers"]["X-API-Key"] == "k"
    assert call["json"]["name"] == "n" and call["json"]["sources"] == [{"path": "a", "content": "b"}]


def test_each_command_hits_its_route(sent):
    c = AgentClient(BASE)
    c.delete_project("p1")
    c.generate_tests("p1", "d", [])
    c.prepare_test("p1", "code")
    c.report_execution("p1", {}, "code")
    c.health()
    assert [(x["method"], x["url"].removeprefix(BASE)) for x in sent] == [
        ("DELETE", "/project/p1"), ("POST", "/generate"), ("POST", "/prepare"),
        ("POST", "/report"), ("GET", "/hello"),
    ]


def test_legacy_mcp_suffix_is_stripped(sent):
    AgentClient(BASE + "/mcp/").generate_tests("p", "", [])
    assert sent[0]["url"] == f"{BASE}/generate"


def test_error_detail_is_reported(sent):
    sent.reply.update(status=400, json={"detail": "git_url 형식 오류"})
    with pytest.raises(ApiError, match="git_url 형식 오류") as info:
        AgentClient(BASE).create_project("n", "x", "o")
    assert info.value.status_code == 400


def test_405_names_the_url(sent):
    sent.reply.update(status=405, json={"detail": "Method Not Allowed"})
    with pytest.raises(ApiError, match=r"/project/register.*405"):
        AgentClient(BASE).create_project("n", "x", "o")


def test_describe_shows_method_and_url():
    assert AgentClient(BASE).describe("register_project") == f"POST {BASE}/project/register"
