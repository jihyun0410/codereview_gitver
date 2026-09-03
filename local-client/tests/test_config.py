"""설정 읽기 검증 — BOM 이 붙어도 project_id 를 잃지 않아야 한다."""

from __future__ import annotations

import json

from codetest import config


def _write(repo_root, payload: dict, encoding: str) -> None:
    path = repo_root / config.LOCAL_CONFIG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding=encoding)


PAYLOAD = {
    "project_id": "abc123",
    "server_url": "http://mcp.example:80/mcp/x",
    "api_key": "k",
}


def test_reads_plain_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write(tmp_path, PAYLOAD, "utf-8")

    cfg = config.load(tmp_path)
    assert cfg.project_id == "abc123"
    assert cfg.server_url == "http://mcp.example:80/mcp/x"


def test_reads_utf8_with_bom(tmp_path, monkeypatch):
    """Windows PowerShell 5.1 의 Set-Content -Encoding UTF8 은 BOM 을 붙인다.

    BOM 때문에 설정을 통째로 잃으면 '등록된 프로젝트가 없습니다' 로 오인된다.
    """
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write(tmp_path, PAYLOAD, "utf-8-sig")

    assert (tmp_path / config.LOCAL_CONFIG_RELPATH).read_bytes().startswith(b"\xef\xbb\xbf")

    cfg = config.load(tmp_path)
    assert cfg.project_id == "abc123"
    assert cfg.server_url == "http://mcp.example:80/mcp/x"


def test_broken_json_falls_back_without_raising(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    path = tmp_path / config.LOCAL_CONFIG_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 이건 JSON 이 아니다", encoding="utf-8")

    assert config.load(tmp_path).project_id is None


def test_save_project_id_keeps_the_other_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "GLOBAL_CONFIG_PATH", tmp_path / "none.json")
    _write(tmp_path, {"server_url": "http://mcp.example:80/mcp/x"}, "utf-8-sig")

    config.save_project_id(tmp_path, "p9")

    cfg = config.load(tmp_path)
    assert cfg.project_id == "p9"
    # BOM 파일을 읽어 합쳤으므로 기존 server_url 이 살아 있어야 한다
    assert cfg.server_url == "http://mcp.example:80/mcp/x"
