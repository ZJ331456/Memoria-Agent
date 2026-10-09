from __future__ import annotations

import hashlib
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest

from memoria.governance import GovernanceError, MemoryGovernance
from memoria.store import Store


@pytest.fixture
def governance(tmp_path):
    store = Store(tmp_path / "governance.db", vector_backend="json")
    layer = MemoryGovernance(store)
    try:
        yield store, layer
    finally:
        store.close()


def _agents(layer: MemoryGovernance):
    owner = layer.create_agent("owner")
    reader = layer.create_agent("reader")
    curator = layer.create_agent("curator")
    outsider = layer.create_agent("outsider")
    space = layer.create_space(owner["id"], "project", "shared")
    layer.grant(owner["id"], space["id"], reader["id"], "reader")
    layer.grant(owner["id"], space["id"], curator["id"], "curator")
    return owner, reader, curator, outsider, space


def _code(error: pytest.ExceptionInfo[GovernanceError], expected: int):
    assert error.value.status_code == expected


def test_agent_token_is_one_time_and_disable_revokes_access(governance):
    store, layer = governance
    agent = layer.create_agent("researcher")
    digest = hashlib.sha256(agent["token"].encode()).hexdigest()
    row = store.db.execute("SELECT * FROM agents WHERE id=?", (agent["id"],)).fetchone()
    assert row["token_hash"] == digest
    assert agent["token"] not in str(dict(row))
    assert layer.authenticate(agent["token"])["id"] == agent["id"]
    assert "token" not in layer.authenticate(agent["token"])
    assert "token_hash" not in layer.list_agents()[0]

    assert layer.disable_agent(agent["id"]) is True
    assert layer.disable_agent(agent["id"]) is False
    assert layer.authenticate(agent["token"]) is None
    with pytest.raises(GovernanceError) as error:
        layer.create_space(agent["id"], "disabled")
    _code(error, 403)


def test_key_rotation_restores_disabled_owner_and_audits_related_spaces(governance):
    store, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    private = layer.create_space(owner["id"], "owner private", "private")
    assert layer.disable_agent(owner["id"]) is True
    assert layer.authenticate(owner["token"]) is None
    assert layer.visible_spaces(curator["id"])[0]["id"] == space["id"]
    assert any(
        event["action"] == "disable_agent" and event["source_ref"] == owner["id"]
        for event in layer.events(curator["id"], space["id"])
    )
    rotated = layer.rotate_agent_key(owner["id"])
    assert rotated["id"] == owner["id"]
    assert rotated["enabled"] == 1
    assert rotated["token"] != owner["token"]
    assert layer.authenticate(owner["token"]) is None
    assert layer.authenticate(rotated["token"])["id"] == owner["id"]
    assert {item["id"] for item in layer.visible_spaces(owner["id"])} == {space["id"], private["id"]}
    assert [event["action"] for event in layer.events(curator["id"], space["id"])[:2]] == [
        "rotate_agent_key", "disable_agent"
    ]
    assert [event["action"] for event in layer.events(owner["id"], private["id"])[:2]] == [
        "rotate_agent_key", "disable_agent"
    ]
    stored = store.db.execute("SELECT token_hash FROM agents WHERE id=?", (owner["id"],)).fetchone()
    assert stored["token_hash"] == hashlib.sha256(rotated["token"].encode()).hexdigest()
    with pytest.raises(GovernanceError) as error:
        layer.rotate_agent_key("missing")
    _code(error, 404)


def test_cross_agent_review_read_isolation_and_audit(governance):
    store, layer = governance
    owner, reader, curator, outsider, space = _agents(layer)
    session = store.create_session("来源对话")
    message = store.add_message(session["id"], "user", "项目使用 SQLite 保存记忆")
    proposal = layer.propose(
        owner["id"], space["id"], "项目使用 SQLite 保存记忆",
        "fact", 4, "storage.engine", "conversation", message["id"],
    )
    assert proposal["status"] == "pending"
    assert proposal["conflict_memory_id"] is None
    assert layer.search(reader["id"], "SQLite", space["id"]) == []
    assert layer.list_proposals(curator["id"], space["id"])[0]["id"] == proposal["id"]
    with pytest.raises(GovernanceError) as error:
        layer.list_proposals(reader["id"], space["id"])
    _code(error, 403)

    memory = layer.approve(curator["id"], proposal["id"], reason="来源已核对")
    assert memory["action"] == "activate"
    assert memory["source_type"] == "conversation"
    assert memory["source_ref"] == message["id"]
    assert memory["version"] == 1
    assert [item["id"] for item in layer.search(reader["id"], "SQLite", space["id"])] == [memory["id"]]
    assert layer.detail(reader["id"], memory["id"])["id"] == memory["id"]
    assert layer.lineage(reader["id"], memory["id"])[0]["id"] == memory["id"]
    assert layer.list_proposals(curator["id"], space["id"], "approved")[0]["applied_memory_id"] == memory["id"]

    for action in (
        lambda: layer.search(outsider["id"], "SQLite", space["id"]),
        lambda: layer.detail(outsider["id"], memory["id"]),
        lambda: layer.lineage(outsider["id"], memory["id"]),
        lambda: layer.events(outsider["id"], space["id"]),
    ):
        with pytest.raises(GovernanceError) as error:
            action()
        _code(error, 403)
    assert layer.search(outsider["id"], "SQLite") == []

    events = layer.events(curator["id"], space["id"])
    assert [item["action"] for item in events[:3]] == ["activate", "propose", "grant"]
    assert events[0]["actor_id"] == curator["id"]
    assert events[0]["reason"] == "来源已核对"
    assert events[0]["source_ref"] == message["id"]
    with pytest.raises(GovernanceError) as error:
        layer.events(reader["id"], space["id"])
    _code(error, 403)
    with pytest.raises(sqlite3.DatabaseError):
        store.db.execute("UPDATE governance_events SET reason='tampered' WHERE seq=?", (events[0]["seq"],))
    store.db.rollback()


def test_grants_private_space_and_rejection(governance):
    _, layer = governance
    owner, reader, curator, outsider, space = _agents(layer)
    grants = layer.list_grants(curator["id"], space["id"])
    assert {item["agent_id"] for item in grants} == {reader["id"], curator["id"]}
    assert all("agent_name" in item for item in grants)
    with pytest.raises(GovernanceError) as error:
        layer.list_grants(reader["id"], space["id"])
    _code(error, 403)
    with pytest.raises(GovernanceError) as error:
        layer.grant(curator["id"], space["id"], outsider["id"], "reader")
    _code(error, 403)
    with pytest.raises(GovernanceError) as error:
        layer.propose(reader["id"], space["id"], "reader write", topic_key="reader.write")
    _code(error, 403)

    layer.grant(owner["id"], space["id"], outsider["id"], "contributor")
    rejected = layer.propose(outsider["id"], space["id"], "错误结论", topic_key="claim")
    assert [item["id"] for item in layer.list_proposals(outsider["id"], space["id"])] == [rejected["id"]]
    assert layer.reject(curator["id"], rejected["id"], "证据不足")["status"] == "rejected"
    assert layer.search(reader["id"], "错误结论", space["id"]) == []
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], rejected["id"])
    _code(error, 409)
    assert layer.revoke_grant(owner["id"], space["id"], outsider["id"]) is True
    assert layer.revoke_grant(owner["id"], space["id"], outsider["id"]) is False
    with pytest.raises(GovernanceError) as error:
        layer.list_proposals(outsider["id"], space["id"])
    _code(error, 403)

    private = layer.create_space(owner["id"], "secret", "private")
    own = layer.propose(owner["id"], private["id"], "只有 owner 可读", topic_key="secret")
    layer.approve(owner["id"], own["id"])
    assert layer.search(owner["id"], "owner", private["id"])
    with pytest.raises(GovernanceError) as error:
        layer.grant(owner["id"], private["id"], reader["id"], "reader")
    _code(error, 409)
    with pytest.raises(GovernanceError) as error:
        layer.search(reader["id"], "owner", private["id"])
    _code(error, 403)


def test_conflict_requires_current_version_and_preserves_lineage(governance):
    _, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    first = layer.propose(owner["id"], space["id"], "计划使用 PostgreSQL", topic_key="storage.engine")
    old = layer.approve(curator["id"], first["id"])
    second = layer.propose(owner["id"], space["id"], "计划使用 SQLite", topic_key="storage.engine")
    assert second["conflict_memory_id"] == old["id"]
    assert layer.list_proposals(curator["id"], space["id"])[0]["conflict_memory_id"] == old["id"]
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], second["id"])
    _code(error, 409)
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], second["id"], expected_replaces_id="stale")
    _code(error, 409)
    assert layer.detail(reader["id"], old["id"])["status"] == "active"

    new = layer.approve(
        curator["id"], second["id"], reason="需求变更", expected_replaces_id=old["id"]
    )
    assert new["action"] == "supersede"
    assert new["version"] == 2
    assert new["supersedes_id"] == old["id"]
    assert layer.detail(reader["id"], old["id"])["status"] == "superseded"
    assert [item["version"] for item in layer.lineage(reader["id"], new["id"])] == [1, 2]
    assert [item["id"] for item in layer.search(reader["id"], "计划", space["id"])] == [new["id"]]
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], second["id"], expected_replaces_id=old["id"])
    _code(error, 409)

    duplicate = layer.propose(owner["id"], space["id"], "计划使用 SQLite", topic_key="storage.engine")
    assert duplicate["conflict_memory_id"] is None
    reused = layer.approve(curator["id"], duplicate["id"])
    assert reused["action"] == "confirm"
    assert reused["id"] == new["id"]
    assert len(layer.lineage(reader["id"], new["id"])) == 2


@pytest.mark.parametrize(
    "changes",
    [
        {"source_type": "manual"},
        {"source_ref": "external-v2"},
        {"expires_at": "2035-01-01T00:00:00+00:00"},
    ],
)
def test_same_text_with_changed_source_or_expiry_creates_new_version(governance, changes):
    _, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    original_proposal = layer.propose(
        owner["id"], space["id"], "共享事实", topic_key="same-text",
        source_type="external", source_ref="external-v1",
    )
    original = layer.approve(curator["id"], original_proposal["id"])
    proposed = layer.propose(
        owner["id"], space["id"], "共享事实", topic_key="same-text",
        source_type=changes.get("source_type", "external"),
        source_ref=changes.get("source_ref", "external-v1"),
        expires_at=changes.get("expires_at"),
    )
    assert proposed["conflict_memory_id"] == original["id"]
    assert layer.list_proposals(curator["id"], space["id"])[0]["conflict_memory_id"] == original["id"]
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], proposed["id"])
    _code(error, 409)
    current = layer.approve(
        curator["id"], proposed["id"], expected_replaces_id=original["id"]
    )
    assert current["action"] == "supersede"
    assert current["version"] == 2
    assert current["id"] != original["id"]
    assert current["source_type"] == proposed["source_type"]
    assert current["source_ref"] == proposed["source_ref"]
    assert current["expires_at"] == proposed["expires_at"]
    assert [item["id"] for item in layer.search(reader["id"], "共享", space["id"])] == [current["id"]]


def test_pending_conflict_is_recomputed_after_other_approvals_and_expiry(governance, monkeypatch):
    _, layer = governance
    owner, _, curator, _, space = _agents(layer)
    pending = layer.propose(owner["id"], space["id"], "待审 A", topic_key="topic")
    assert pending["submitted_conflict_memory_id"] is None
    competing = layer.propose(owner["id"], space["id"], "已审 B", topic_key="topic")
    first = layer.approve(curator["id"], competing["id"])
    listed = next(item for item in layer.list_proposals(curator["id"], space["id"]) if item["id"] == pending["id"])
    assert listed["submitted_conflict_memory_id"] is None
    assert listed["conflict_memory_id"] == first["id"]
    replacement = layer.propose(owner["id"], space["id"], "已审 C", topic_key="topic")
    second = layer.approve(curator["id"], replacement["id"], expected_replaces_id=first["id"])
    listed = next(item for item in layer.list_proposals(curator["id"], space["id"]) if item["id"] == pending["id"])
    assert listed["conflict_memory_id"] == second["id"]
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], pending["id"], expected_replaces_id=first["id"])
    _code(error, 409)

    expires = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    expiring = layer.propose(
        owner["id"], space["id"], "临时", topic_key="temporary", expires_at=expires
    )
    current = layer.approve(curator["id"], expiring["id"])
    waiting = layer.propose(owner["id"], space["id"], "长期", topic_key="temporary")
    assert waiting["submitted_conflict_memory_id"] == current["id"]
    monkeypatch.setattr(
        "memoria.governance.now",
        lambda: (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat(),
    )
    listed = next(item for item in layer.list_proposals(curator["id"], space["id"]) if item["id"] == waiting["id"])
    assert listed["submitted_conflict_memory_id"] == current["id"]
    assert listed["conflict_memory_id"] is None
    assert layer.approve(curator["id"], waiting["id"])["action"] == "activate"


def test_proposer_loses_grant_or_is_disabled_before_approval(governance):
    _, layer = governance
    owner, _, curator, outsider, space = _agents(layer)
    layer.grant(owner["id"], space["id"], outsider["id"], "contributor")
    revoked = layer.propose(outsider["id"], space["id"], "撤权后不能发布", topic_key="revoked-grant")
    assert layer.revoke_grant(owner["id"], space["id"], outsider["id"])
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], revoked["id"])
    _code(error, 409)
    assert layer.reject(curator["id"], revoked["id"], "提议人已失去权限")["status"] == "rejected"

    disabled = layer.propose(owner["id"], space["id"], "停用后不能发布", topic_key="disabled-owner")
    assert layer.disable_agent(owner["id"])
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], disabled["id"])
    _code(error, 409)
    assert layer.reject(curator["id"], disabled["id"], "提议人密钥停用")["status"] == "rejected"


def test_revoke_current_version_hides_all_old_version_ids_from_members(governance):
    _, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    first_proposal = layer.propose(owner["id"], space["id"], "版本一", topic_key="fact")
    first = layer.approve(curator["id"], first_proposal["id"])
    second_proposal = layer.propose(owner["id"], space["id"], "版本二", topic_key="fact")
    second = layer.approve(
        curator["id"], second_proposal["id"], expected_replaces_id=first["id"]
    )
    assert layer.detail(reader["id"], first["id"])["status"] == "superseded"
    layer.revoke_memory(curator["id"], second["id"], "用户要求遗忘")
    for memory_id in (first["id"], second["id"]):
        for action in (
            lambda memory_id=memory_id: layer.detail(reader["id"], memory_id),
            lambda memory_id=memory_id: layer.lineage(reader["id"], memory_id),
        ):
            with pytest.raises(GovernanceError) as error:
                action()
            _code(error, 404)
    assert layer.search(reader["id"], "版本", space["id"]) == []
    assert [item["status"] for item in layer.lineage(curator["id"], second["id"])] == [
        "revoked", "revoked"
    ]
    audit = layer.events(curator["id"], space["id"])
    assert any(event["action"] == "revoke_history" and event["memory_id"] == first["id"] for event in audit)


def test_concurrent_approval_compare_and_swap(tmp_path):
    path = tmp_path / "race.db"
    store_a = Store(path, vector_backend="json")
    store_b = Store(path, vector_backend="json")
    layer_a = MemoryGovernance(store_a)
    layer_b = MemoryGovernance(store_b)
    try:
        owner = layer_a.create_agent("owner")
        space = layer_a.create_space(owner["id"], "race")
        original = layer_a.approve(
            owner["id"],
            layer_a.propose(owner["id"], space["id"], "v1", topic_key="version")["id"],
        )
        proposed = [
            layer_a.propose(owner["id"], space["id"], "v2", topic_key="version"),
            layer_a.propose(owner["id"], space["id"], "v3", topic_key="version"),
        ]

        def approve(layer, proposal):
            try:
                return layer.approve(
                    owner["id"], proposal["id"], expected_replaces_id=original["id"]
                )["id"]
            except GovernanceError as exc:
                return exc.status_code

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(approve, layer_a, proposed[0]),
                pool.submit(approve, layer_b, proposed[1]),
            ]
            results = [future.result(timeout=10) for future in futures]
        assert sum(result == 409 for result in results) == 1
        assert sum(isinstance(result, str) for result in results) == 1
        assert len(layer_a.search(owner["id"], "", space["id"])) == 1
        assert len(layer_a.lineage(owner["id"], results[0] if isinstance(results[0], str) else results[1])) == 2
    finally:
        store_b.close()
        store_a.close()


def test_expiration_revocation_and_legacy_reference(governance, monkeypatch):
    store, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    legacy = store.add_memory("旧库独立记录", source="manual")
    expiry = (datetime.now(timezone.utc) + timedelta(minutes=1)).isoformat()
    proposal = layer.propose(
        owner["id"], space["id"], "限时共享事实", topic_key="expiry",
        source_type="legacy_memory", source_ref=legacy["id"], expires_at=expiry,
    )
    memory = layer.approve(curator["id"], proposal["id"])
    assert layer.search(reader["id"], "限时", space["id"])[0]["id"] == memory["id"]
    assert layer.detail(reader["id"], memory["id"])["source_ref"] == legacy["id"]
    assert layer.search(reader["id"], "旧库独立记录", space["id"]) == []

    later = (datetime.now(timezone.utc) + timedelta(minutes=2)).isoformat()
    monkeypatch.setattr("memoria.governance.now", lambda: later)
    assert layer.search(reader["id"], "限时", space["id"]) == []
    for action in (
        lambda: layer.detail(reader["id"], memory["id"]),
        lambda: layer.lineage(reader["id"], memory["id"]),
    ):
        with pytest.raises(GovernanceError) as error:
            action()
        _code(error, 404)
    assert layer.detail(curator["id"], memory["id"])["source_ref"] == legacy["id"]
    assert layer.detail(curator["id"], memory["id"])["effective_status"] == "expired"
    assert layer.lineage(curator["id"], memory["id"])[0]["effective_status"] == "expired"
    replacement = layer.propose(owner["id"], space["id"], "长期事实", topic_key="expiry")
    assert replacement["conflict_memory_id"] is None
    active = layer.approve(curator["id"], replacement["id"])
    assert active["version"] == 2
    assert layer.detail(curator["id"], memory["id"])["status"] == "revoked"
    assert [item["version"] for item in layer.lineage(reader["id"], active["id"])] == [2]
    assert [item["version"] for item in layer.lineage(curator["id"], active["id"])] == [1, 2]
    assert layer.revoke_memory(curator["id"], active["id"], "不再适用")["status"] == "revoked"
    assert layer.search(reader["id"], "长期", space["id"]) == []
    for action in (
        lambda: layer.detail(reader["id"], active["id"]),
        lambda: layer.lineage(reader["id"], active["id"]),
    ):
        with pytest.raises(GovernanceError) as error:
            action()
        _code(error, 404)
    assert [item["status"] for item in layer.lineage(curator["id"], active["id"])] == ["revoked", "revoked"]
    with pytest.raises(GovernanceError) as error:
        layer.revoke_memory(curator["id"], active["id"])
    _code(error, 409)
    assert {item["action"] for item in layer.events(curator["id"], space["id"])} >= {
        "expire", "revoke", "activate"
    }


def test_legacy_source_must_still_be_active_when_approved(governance):
    store, layer = governance
    owner, _, curator, _, space = _agents(layer)
    source = store.add_memory("旧库中一条事实", "fact", 3, "manual")
    imported = layer.propose(
        owner["id"], space["id"], source["content"], topic_key="legacy",
        source_type="legacy_memory", source_ref=source["id"],
    )
    assert store.delete_memory(source["id"])
    with pytest.raises(GovernanceError) as error:
        layer.approve(curator["id"], imported["id"])
    _code(error, 409)
    assert layer.search(owner["id"], "旧库", space["id"]) == []
    assert layer.reject(curator["id"], imported["id"], "原来源已删除")["status"] == "rejected"


def test_validation_and_denied_source_access(governance):
    _, layer = governance
    owner, reader, curator, _, space = _agents(layer)
    with pytest.raises(GovernanceError) as error:
        layer.propose(owner["id"], space["id"], "fact", topic_key="")
    _code(error, 422)
    with pytest.raises(GovernanceError) as error:
        layer.propose(
            owner["id"], space["id"], "fact", topic_key="fact",
            source_type="conversation", source_ref=None,
        )
    _code(error, 422)
    with pytest.raises(GovernanceError) as error:
        layer.propose(
            owner["id"], space["id"], "fact", topic_key="fact",
            source_type="message", source_ref=None,
        )
    _code(error, 422)
    with pytest.raises(GovernanceError) as error:
        layer.propose(owner["id"], space["id"], "fact", topic_key="fact", expires_at="2025-01-01")
    _code(error, 422)
    with pytest.raises(GovernanceError) as error:
        layer.propose(reader["id"], space["id"], "fact", topic_key="fact")
    _code(error, 403)
    proposal = layer.propose(owner["id"], space["id"], "fact", topic_key="fact")
    with pytest.raises(GovernanceError) as error:
        layer.approve(reader["id"], proposal["id"])
    _code(error, 403)
    assert layer.approve(curator["id"], proposal["id"])["status"] == "active"
