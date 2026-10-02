from pathlib import Path

from fastapi.testclient import TestClient

from memoria.api import create_app


def _client(tmp_path: Path) -> TestClient:
    config = tmp_path / "config.toml"
    config.write_text(
        f'''[llm.main]\nmodel="test"\napi_key="x"\nbase_url="http://example.test/v1"\n'''
        f'''[server.security]\napi_token="admin-secret"\n'''
        f'''[agent.mcp]\nenabled=false\n'''
        f'''[storage]\ndatabase="{tmp_path / 'governance.db'}"\n''',
        encoding="utf-8",
    )
    return TestClient(create_app(config))


def test_shared_memory_review_acl_versions_and_key_revocation(tmp_path: Path):
    with _client(tmp_path) as client:
        admin = {"Authorization": "Bearer admin-secret"}
        agents = {}
        for name in ("owner", "writer", "reader", "outsider"):
            response = client.post("/api/governance/agents", headers=admin, json={"name": name})
            assert response.status_code == 200, response.text
            agents[name] = response.json()
        assert all("token" not in row for row in client.get("/api/governance/agents", headers=admin).json())

        def key(name: str) -> dict[str, str]:
            return {"X-Agent-Key": agents[name]["token"]}

        assert client.get("/api/memories", headers=key("owner")).status_code == 401
        assert client.get("/api/governance/agents", headers=key("owner")).status_code == 401

        space_response = client.post("/api/shared/spaces", headers=key("owner"), json={"name": "研究组", "visibility": "shared"})
        assert space_response.status_code == 200, space_response.text
        space = space_response.json()
        space_id = space["id"]
        assert client.get("/api/shared/spaces", headers=key("outsider")).json() == []
        assert client.get("/api/shared/memories", headers=key("outsider"), params={"space_id": space_id}).status_code in {403, 404}

        for name, role in (("writer", "contributor"), ("reader", "reader")):
            response = client.post(
                f"/api/shared/spaces/{space_id}/grants", headers=key("owner"),
                json={"agent_id": agents[name]["id"], "role": role},
            )
            assert response.status_code == 200, response.text
        assert client.get(f"/api/shared/spaces/{space_id}/grants", headers=key("reader")).status_code == 403

        candidate = {
            "space_id": space_id, "content": "团队采用 PostgreSQL 作为主存储", "kind": "fact",
            "topic_key": "team:primary_database", "source_type": "external", "source_ref": "design-note-1",
        }
        assert client.post("/api/shared/proposals", headers=key("reader"), json=candidate).status_code == 403
        proposal_response = client.post("/api/shared/proposals", headers=key("writer"), json=candidate)
        assert proposal_response.status_code == 200, proposal_response.text
        proposal = proposal_response.json()
        assert proposal["status"] == "pending"
        assert client.get("/api/shared/memories", headers=key("reader"), params={"space_id": space_id}).json() == []
        assert client.get("/api/shared/proposals", headers=key("reader"), params={"space_id": space_id}).status_code == 403

        assert client.post(
            f"/api/shared/proposals/{proposal['id']}/approve", headers=key("owner"),
            json={"reason": "   "},
        ).status_code == 422

        approved_response = client.post(
            f"/api/shared/proposals/{proposal['id']}/approve", headers=key("owner"),
            json={"reason": "已核对团队设计记录"},
        )
        assert approved_response.status_code == 200, approved_response.text
        first = approved_response.json()
        result = client.get("/api/shared/memories", headers=key("reader"), params={"space_id": space_id, "q": "PostgreSQL"})
        assert [item["id"] for item in result.json()] == [first["id"]]
        assert client.get(f"/api/shared/memories/{first['id']}", headers=key("outsider")).status_code in {403, 404}
        assert client.get("/api/shared/events", headers=key("reader"), params={"space_id": space_id}).status_code == 403

        changed = client.post(
            "/api/shared/proposals", headers=key("writer"),
            json={**candidate, "content": "团队采用 SQLite 作为主存储", "source_ref": "design-note-2"},
        ).json()
        assert changed["conflict_memory_id"] == first["id"]
        assert client.post(
            f"/api/shared/proposals/{changed['id']}/approve", headers=key("owner"),
            json={"reason": "新设计记录已生效"},
        ).status_code == 409
        revised_response = client.post(
            f"/api/shared/proposals/{changed['id']}/approve", headers=key("owner"),
            json={"reason": "新设计记录已生效", "expected_replaces_id": first["id"]},
        )
        assert revised_response.status_code == 200, revised_response.text
        revised = revised_response.json()
        current = client.get("/api/shared/memories", headers=key("reader"), params={"space_id": space_id}).json()
        assert [item["id"] for item in current] == [revised["id"]]
        lineage = client.get(f"/api/shared/memories/{revised['id']}/lineage", headers=key("reader")).json()
        assert {item["id"] for item in lineage} == {first["id"], revised["id"]}
        assert client.get("/api/shared/events", headers=key("owner"), params={"space_id": space_id}).json()

        assert client.post(f"/api/shared/memories/{revised['id']}/revoke", headers=key("writer"), json={"reason": "请求删除"}).status_code == 403
        assert client.post(f"/api/shared/memories/{revised['id']}/revoke", headers=key("owner"), json={"reason": "设计再次变更"}).status_code == 200
        assert client.get("/api/shared/memories", headers=key("reader"), params={"space_id": space_id}).json() == []
        assert client.get(f"/api/shared/memories/{first['id']}", headers=key("reader")).status_code == 404
        assert client.get(f"/api/shared/memories/{revised['id']}/lineage", headers=key("reader")).status_code == 404

        assert client.delete(f"/api/governance/agents/{agents['reader']['id']}", headers=admin).status_code == 204
        assert client.get("/api/shared/spaces", headers=key("reader")).status_code == 401
        rotated = client.post(f"/api/governance/agents/{agents['reader']['id']}/rotate-key", headers=admin)
        assert rotated.status_code == 200, rotated.text
        assert rotated.json()["token"] != agents["reader"]["token"]
        assert client.get("/api/shared/spaces", headers=key("reader")).status_code == 401
        assert client.get("/api/shared/spaces", headers={"X-Agent-Key": rotated.json()["token"]}).status_code == 200

        # A forged Agent ID or server-wide token cannot stand in for an Agent key.
        assert client.get("/api/shared/spaces", headers={"X-Agent-Key": agents["owner"]["id"]}).status_code == 401
        assert client.get("/api/shared/spaces", headers=admin).status_code == 401


def test_legacy_memory_import_requires_admin_and_review(tmp_path: Path):
    with _client(tmp_path) as client:
        admin = {"Authorization": "Bearer admin-secret"}
        owner = client.post("/api/governance/agents", headers=admin, json={"name": "owner"}).json()
        key = {"X-Agent-Key": owner["token"]}
        space = client.post("/api/shared/spaces", headers=key, json={"name": "知识库"}).json()
        legacy = client.app.state.store.add_memory("团队文档在内网 Wiki", kind="fact", importance=3)
        url = f"/api/governance/spaces/{space['id']}/import-memory/{legacy['id']}"
        body = {"actor_id": owner["id"], "topic_key": "team:docs_location"}
        assert client.post(url, headers=key, json=body).status_code == 401
        imported = client.post(url, headers=admin, json=body)
        assert imported.status_code == 200, imported.text
        assert imported.json()["source_type"] == "legacy_memory"
        assert imported.json()["source_ref"] == legacy["id"]
        assert client.get("/api/shared/memories", headers=key, params={"space_id": space["id"]}).json() == []
        reviewed = client.post(
            f"/api/shared/proposals/{imported.json()['id']}/approve", headers=key,
            json={"reason": "核对 Wiki 路径"},
        )
        assert reviewed.status_code == 200, reviewed.text
        assert reviewed.json()["source_ref"] == legacy["id"]

        other = client.app.state.store.add_memory("即将删除的旧记忆", kind="fact")
        pending = client.post(
            f"/api/governance/spaces/{space['id']}/import-memory/{other['id']}", headers=admin,
            json={"actor_id": owner["id"], "topic_key": "deleted-source"},
        ).json()
        client.app.state.store.delete_memory(other["id"])
        assert client.post(
            f"/api/shared/proposals/{pending['id']}/approve", headers=key,
            json={"reason": "来源已删除"},
        ).status_code == 409
