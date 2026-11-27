"""Settings-store tests: DB-backed config, UI editing, provider probe."""
import json
import os

import pytest

DB = "/tmp/vataic-settings-test.db"


@pytest.fixture(autouse=True)
def _db(monkeypatch):
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(DB + suf)
        except OSError:
            pass
    monkeypatch.setenv("DATABASE_PATH", DB)
    monkeypatch.setenv("ARTIFACT_DIR", "/tmp/vataic-settings-artifacts")
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:1")
    monkeypatch.setenv("LLM_REQUEST_TIMEOUT_S", "1")
    monkeypatch.setenv("LLM_MAX_ATTEMPTS", "1")
    monkeypatch.setenv("VATAIC_ALLOW_HEURISTIC", "1")
    import importlib
    import app.config as cfg
    importlib.reload(cfg)
    import app.llm.client as cl
    cl.reset_breaker()
    from app.migrate import apply_pending
    from app.seed import seed
    apply_pending(DB)
    seed(DB)
    cfg.refresh()
    yield
    for suf in ("", "-wal", "-shm"):
        try:
            os.remove(DB + suf)
        except OSError:
            pass


def test_config_reads_db_rows_over_env(monkeypatch):
    """A non-empty DB settings row wins over the env bootstrap value."""
    monkeypatch.setenv("LLM_BASE_URL", "http://env-host:1/v1")
    import importlib
    import app.config as cfg
    with __import__("app.db", fromlist=["get_conn"]).get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.base_url','http://db-host:2/v1')")
    cfg.refresh()
    assert cfg.settings.llm_base_url == "http://db-host:2/v1"


def test_config_env_fallback_when_no_db_row(monkeypatch):
    """With no DB row (empty seeded row counts as unset) env still applies."""
    monkeypatch.setenv("LLM_STRONG_MODEL", "env-strong-model")
    import importlib
    import app.config as cfg
    with __import__("app.db", fromlist=["get_conn"]).get_conn(DB) as c:
        c.execute("DELETE FROM settings WHERE key='llm.strong_model'")
    cfg.refresh()
    assert cfg.settings.llm_strong_model == "env-strong-model"


def test_api_key_and_secret_are_env_only(monkeypatch):
    """LLM_API_KEY / FLASK_SECRET_KEY never come from the settings table."""
    from app.config import flask_secret_key, llm_api_key
    monkeypatch.setenv("LLM_API_KEY", "sk-env-key")
    monkeypatch.setenv("FLASK_SECRET_KEY", "sekrit")
    import importlib
    import app.config as cfg
    with __import__("app.db", fromlist=["get_conn"]).get_conn(DB) as c:
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('llm.api_key','sk-db-leak')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('secret_key','db-leak')")
    assert llm_api_key() == "sk-env-key"
    assert flask_secret_key() == "sekrit"
    # and the save path refuses such keys outright
    from app.settings_store import save_many
    saved, errors = save_many({"llm.api_key": "sk-x", "secret_key": "y"})
    assert saved == [] and errors == []
    with __import__("app.db", fromlist=["get_conn"]).get_conn(DB) as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert "sk-x" not in rows.values() and "y" not in rows.values()


def test_save_many_coerces_and_rejects():
    from app.settings_store import ALLOWED, save_many
    assert "llm.api_key" not in ALLOWED and "secret_key" not in ALLOWED
    saved, errors = save_many({
        "budget.max_steps": "55",
        "merge.distance_threshold": "9.5",
        "llm.base_url": "http://newhost:1234/v1",
        "beam.width": "not-a-number",
        "llm.base_url_x": "http://ignored",
    })
    assert set(saved) == {"budget.max_steps", "merge.distance_threshold", "llm.base_url"}
    assert any("beam.width" in e for e in errors)
    from app.db import get_conn
    with get_conn(DB) as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert rows["budget.max_steps"] == "55"
    assert rows["merge.distance_threshold"] == "9.5"
    assert rows["llm.base_url"] == "http://newhost:1234/v1"


def test_saved_settings_flow_into_settings_object():
    """After a save, app.config.settings serves the new values (no restart)."""
    from app.settings_store import save_many
    from app.config import settings
    save_many({"beam.width": "20", "branch.prob_threshold": "0.3"})
    import app.config as cfg
    cfg.refresh()
    assert settings.beam_width == 20
    assert abs(settings.branch_prob_threshold - 0.3) < 1e-9


def test_migration_012_renames_and_purges():
    """012 renames legacy flat keys and drops the retired LLM cap rows."""
    from app.db import get_conn
    from app.migrate import apply_pending
    import app.seed as seed_mod
    seed_mod.DEFAULTS.pop("branch.prob_threshold", None)
    with get_conn(DB) as c:
        c.execute("DELETE FROM settings")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('branch_prob_threshold','0.2')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('budget.max_llm_calls','500')")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('budget.max_tokens','999')")
        c.execute("DELETE FROM schema_migrations WHERE version='012_settings_consolidation'")
    apply_pending(DB)
    with get_conn(DB) as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert rows.get("branch.prob_threshold") == "0.2"
    assert "branch_prob_threshold" not in rows
    assert "budget.max_llm_calls" not in rows and "budget.max_tokens" not in rows


def test_settings_page_renders_editable_form_and_probe():
    from app import create_app
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.get("/settings")
    assert r.status_code == 200
    body = r.get_data(as_text=True)
    # editable inputs named by settings key
    assert 'name="llm.base_url"' in body
    assert 'name="llm.primary_model"' in body
    assert 'name="budget.max_steps"' in body
    assert 'name="beam.width"' in body
    # save + test controls
    assert "/settings" in body and "Test Provider" in body
    # env-key row still shown as read-only state
    assert "LLM_API_KEY" in body


def test_settings_save_route_writes_db_and_redirects():
    from app import create_app
    from app.db import get_conn
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.post("/settings", data={"budget.max_steps": "77", "beam.width": "9"})
    assert r.status_code == 302
    with get_conn(DB) as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert rows["budget.max_steps"] == "77"
    assert rows["beam.width"] == "9"


def test_settings_save_refuses_non_editable_and_bad_values():
    from app import create_app
    from app.db import get_conn
    app = create_app({"TESTING": True})
    cl = app.test_client()
    r = cl.post("/settings", data={"llm.api_key": "sk-leak", "beam.width": "abc"})
    assert r.status_code == 302
    with get_conn(DB) as c:
        rows = {r["key"]: r["value"] for r in c.execute("SELECT key,value FROM settings")}
    assert "sk-leak" not in rows.values()
    assert rows["beam.width"] != "abc"  # unchanged (validation refused)


def test_test_llm_uses_form_values_not_saved(monkeypatch):
    """Test Provider must probe the form values first (unsaved edits)."""
    from app import create_app
    import app.settings_store as ss
    app = create_app({"TESTING": True})
    cl = app.test_client()
    captured = {}

    def fake_probe(base_url, api_key, model):
        captured["args"] = (base_url, api_key, model)
        return {"ok": True, "stage": "done", "detail": "fine", "models": [], "latency_ms": 1}

    monkeypatch.setattr(ss, "probe_llm", fake_probe)
    r = cl.post("/settings/test-llm",
                data={"llm.base_url": "http://form-host:9/v1",
                      "llm.primary_model": "form-model",
                      "llm.api_key_test": "sk-form-key"})
    assert r.status_code == 302
    assert captured["args"] == ("http://form-host:9/v1", "sk-form-key", "form-model")


def test_test_llm_falls_back_to_stored_config(monkeypatch):
    from app import create_app
    import app.settings_store as ss
    app = create_app({"TESTING": True})
    cl = app.test_client()
    captured = {}

    def fake_probe(base_url, api_key, model):
        captured["args"] = (base_url, api_key, model)
        return {"ok": False, "stage": "url", "detail": "no base URL", "models": [], "latency_ms": 0}

    monkeypatch.setattr(ss, "probe_llm", fake_probe)
    r = cl.post("/settings/test-llm", data={})
    assert r.status_code == 302
    base, key, model = captured["args"]
    assert base.startswith("http")  # stored/env-configured base URL
    assert model == ""


def test_probe_llm_stages_against_a_fake_provider(monkeypatch):
    """Both stages run: /models listing then a real chat completion."""
    import app.settings_store as ss

    class FakeResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._p = payload
            self.text = ""
        def json(self):
            return self._p

    def fake_get(url, headers=None, timeout=None):
        assert url.endswith("/models")
        return FakeResp(200, {"data": [{"id": "m1"}, {"id": "m2"}]})

    captured = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        captured["url"] = url
        captured["payload"] = json
        return FakeResp(200, {"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(ss.requests, "get", fake_get)
    monkeypatch.setattr(ss.requests, "post", fake_post)
    out = ss.probe_llm("http://fake:1/v1", "sk-k", "m2")
    assert out["ok"] is True and out["stage"] == "done"
    assert captured["url"] == "http://fake:1/v1/chat/completions"
    assert captured["payload"]["model"] == "m2"  # configured model, not first listed
    assert "m1, m2" in out["detail"]


def test_probe_llm_blank_model_uses_first_listed(monkeypatch):
    import app.settings_store as ss

    class FakeResp:
        def __init__(self, status, payload):
            self.status_code = status
            self._p = payload
            self.text = ""
        def json(self):
            return self._p

    monkeypatch.setattr(ss.requests, "get",
                        lambda url, headers=None, timeout=None: FakeResp(200, {"data": [{"id": "first-model"}]}))
    captured = {}
    monkeypatch.setattr(ss.requests, "post",
                        lambda url, headers=None, json=None, timeout=None:
                        captured.update(payload=json) or FakeResp(200, {"choices": [{"message": {"content": "ok"}}]}))
    out = ss.probe_llm("http://fake:1/v1", "", "")
    assert out["ok"] is True
    assert captured["payload"]["model"] == "first-model"


def test_probe_llm_reports_auth_failure(monkeypatch):
    import app.settings_store as ss

    class FakeResp:
        status_code = 401
        text = ""
        def json(self):
            return {}
    monkeypatch.setattr(ss.requests, "get",
                        lambda url, headers=None, timeout=None: FakeResp())
    out = ss.probe_llm("http://fake:1/v1", "bad-key", "")
    assert out["ok"] is False and out["stage"] == "auth"
    assert "API key" in out["detail"]


def test_old_test_helpers_still_exist():
    """Existing tests import _effective; keep the helper importable."""
    from app.routes.settings import _effective
    from app.seed import DEFAULTS
    seeded = dict(DEFAULTS)
    eff = _effective(seeded)
    assert eff["llm.base_url"] == "http://127.0.0.1:1"
    seeded2 = dict(DEFAULTS)
    seeded2["llm.base_url"] = "http://custom:9/v1"
    eff2 = _effective(seeded2)
    assert eff2["llm.base_url"] == "http://custom:9/v1"
