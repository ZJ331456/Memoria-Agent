from pathlib import Path
import asyncio

from fastapi.testclient import TestClient

from memoria.api import create_app
from memoria.config import Settings
from memoria.drift import DriftWorker
from memoria.store import Store


def test_drift_store_and_skip_reasons(tmp_path: Path):
    from zoneinfo import ZoneInfo

    store = Store(tmp_path / "drift.db")
    assert store.seconds_since_last_user_message() is None
    session = store.create_session("测试")
    store.add_message(session["id"], "user", "你好")
    assert store.seconds_since_last_user_message() is not None
    drift = store.create_session("〔Drift〕空闲整理")
    store.add_message(drift["id"], "user", "[Drift] prompt")
    assert store.seconds_since_last_user_message(exclude_session_titles=("〔Drift〕空闲整理",)) is not None
    # Drift-only messages must not reset idle when excluded.
    store2 = Store(tmp_path / "drift2.db")
    only_drift = store2.create_session("〔Drift〕空闲整理")
    store2.add_message(only_drift["id"], "user", "[Drift] alone")
    assert store2.seconds_since_last_user_message(exclude_session_titles=("〔Drift〕空闲整理",)) is None
    run = store.create_drift_run(run_id="r1", session_id=session["id"], skill="drift-digest", trigger="test")
    assert run["status"] == "running"
    finished = store.finish_drift_run("r1", status="completed", summary="ok", steps=2, trace_id="t1")
    assert finished["status"] == "completed" and finished["summary"] == "ok"
    assert store.latest_drift_run()["id"] == "r1"
    assert store.overview()["drift_runs"] == 1
    assert store.drift_runs_today(ZoneInfo("Asia/Shanghai")) >= 1
    stale = store.create_drift_run(run_id="stale", session_id=session["id"], skill="drift-digest", trigger="test")
    store.db.execute("UPDATE drift_runs SET created_at='2000-01-01T00:00:00+00:00' WHERE id=?", (stale["id"],))
    store.db.commit()
    assert store.expire_stale_drift_runs(older_than_seconds=60) >= 1
    assert store.db.execute("SELECT status FROM drift_runs WHERE id='stale'").fetchone()[0] == "failed"


def test_drift_api_status_and_manual_skip(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="x"
base_url="http://example.test/v1"
[agent.drift]
enabled=true
min_idle_seconds=999999
interval_seconds=60
max_steps=3
daily_budget=2
quiet_hours=[0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23]
allowed_skills=["drift-digest"]
allow_write_tools=[]
[storage]
database="{tmp_path / 'api-drift.db'}"
''',
        encoding="utf-8",
    )
    app = create_app(config)
    with TestClient(app) as client:
        status = client.get("/api/drift").json()
        assert status["status"]["enabled"] is True
        assert "drift-digest" in status["status"]["allowed_skills"]
        overview = client.get("/api/overview").json()
        assert "drift" in overview
        skipped = client.post("/api/drift/run", json={"force": False}).json()
        assert skipped["result"].get("skipped") is True
        assert "quiet_hour" in str(skipped["result"].get("reason", ""))


def test_drift_worker_respects_disabled(tmp_path: Path):
    config = tmp_path / "cfg.toml"
    config.write_text(
        f'''[llm.main]
model="test"
api_key="x"
base_url="http://example.test/v1"
[agent.drift]
enabled=false
[storage]
database="{tmp_path / 'w.db'}"
''',
        encoding="utf-8",
    )
    settings = Settings.load(config)
    store = Store(settings.database)
    worker = DriftWorker(store=store, runtime=None, skills=None, markdown=None, settings=settings)
    result = asyncio.run(worker.maybe_run(trigger="test"))
    assert result and result.get("skipped") and result.get("reason") == "disabled"


def test_drift_digest_skill_present():
    root = Path(__file__).resolve().parents[1] / "skills" / "drift-digest" / "SKILL.md"
    assert root.exists()
    text = root.read_text(encoding="utf-8")
    assert "forget_memory" in text and "Drift Digest" in text
