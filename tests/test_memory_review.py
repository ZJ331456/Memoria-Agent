from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app


def test_review_queue_requires_approval_and_links_to_old_source_message(tmp_path: Path):
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n'''
        f'''[memory.markdown]\ndirectory="{tmp_path / 'markdown'}"\n'''
        f'''[storage]\ndatabase="{tmp_path / 'review.db'}"\n''', encoding="utf-8",
    )
    app = create_app(config)
    client = TestClient(app)
    store = app.state.store
    session = store.create_session("来源会话")
    source = store.add_message(session["id"], "user", "我喜欢红茶")
    for index in range(130):
        store.add_message(session["id"], "assistant", f"后续消息 {index}")
    assert source["id"] not in [item["id"] for item in client.get(f"/api/sessions/{session['id']}/messages").json()]

    job = store.enqueue_memory_job(source["id"], source["content"], "收到")
    store.claim_memory_job("worker")
    candidates = store.stage_memory_reviews(job["id"], "worker", [
        {"content": "用户喜欢红茶", "kind": "preference", "importance": 3},
        {"content": "用户的目标是早睡", "kind": "goal", "importance": 2},
    ])
    store.finish_memory_job(job["id"], owner="worker")
    assert len(client.get("/api/memory-reviews").json()) == 2
    assert client.get("/api/memories?q=红茶").json() == []

    resolved = client.get(f"/api/messages/{source['id']}/source")
    assert resolved.status_code == 200
    assert resolved.json()["session_id"] == session["id"]
    anchored = client.get(f"/api/sessions/{session['id']}/messages?anchor_id={source['id']}")
    assert anchored.status_code == 200 and anchored.json()[0]["id"] == source["id"]
    other = store.create_session("其他会话")
    assert client.get(f"/api/sessions/{other['id']}/messages?anchor_id={source['id']}").status_code == 404

    approved = client.post(f"/api/memory-reviews/{candidates[0]['id']}/approve", json={
        "content": "用户现在喜欢乌龙茶", "kind": "preference", "importance": 4,
    })
    assert approved.status_code == 200
    assert approved.json()["status"] == "approved" and approved.json()["applied_action"] == "created"
    memories = client.get("/api/memories").json()
    assert len(memories) == 1 and memories[0]["content"] == "用户现在喜欢乌龙茶"
    assert memories[0]["source_ref"] == source["id"]
    assert client.post(f"/api/memory-reviews/{candidates[0]['id']}/approve", json={
        "content": "重复", "kind": "preference", "importance": 4,
    }).status_code == 409

    rejected = client.post(f"/api/memory-reviews/{candidates[1]['id']}/reject")
    assert rejected.status_code == 200 and rejected.json()["status"] == "rejected"
    assert client.post(f"/api/memory-reviews/{candidates[1]['id']}/reject").status_code == 409
    assert client.get("/api/memory-reviews").json() == []
    assert len(client.get("/api/memory-reviews?status=all").json()) == 2
    assert client.get("/api/memories?q=早睡").json() == []


def test_applying_review_recovers_after_restart(tmp_path: Path):
    from memoria.store import Store

    path = tmp_path / "recover.db"
    store = Store(path)
    job = store.enqueue_memory_job("source", "u", "a")
    store.claim_memory_job("worker")
    review = store.stage_memory_reviews(job["id"], "worker", [{"content": "喜欢散步", "kind": "preference"}])[0]
    store.claim_memory_review(review["id"], review["content"], review["kind"], review["importance"])
    store.close()
    reopened = Store(path)
    assert reopened.memory_review(review["id"])["status"] == "pending"
    reopened.close()
