"""Local source validation and retrieval inside authorized spaces."""

import pytest

from memoria.governance import GovernanceError, MemoryGovernance
from memoria.store import Store


@pytest.fixture
def store(tmp_path):
    instance = Store(tmp_path / "shared.db", "json")
    yield instance
    instance.close()


def test_shared_sources_checked_again_at_approval(store):
    layer = MemoryGovernance(store)
    owner = layer.create_agent("owner")
    space = layer.create_space(owner["id"], "shared", "shared")
    with pytest.raises(GovernanceError):
        layer.propose(owner["id"], space["id"], "用户喜欢茶", topic_key="drink", source_type="message", source_ref="missing")
    session = store.create_session()
    message = store.add_message(session["id"], "user", "用户喜欢茶")
    proposal = layer.propose(owner["id"], space["id"], "用户喜欢茶", topic_key="drink", source_type="message", source_ref=message["id"])
    store.delete_session(session["id"])
    with pytest.raises(GovernanceError):
        layer.approve(owner["id"], proposal["id"])
    assert layer.search(owner["id"], "茶", space["id"]) == []


def test_shared_bm25_query_is_scoped_before_top_k(store):
    layer = MemoryGovernance(store)
    owner = layer.create_agent("owner")
    space = layer.create_space(owner["id"], "visible", "shared")
    outsider = layer.create_agent("outsider")
    hidden = layer.create_space(outsider["id"], "hidden", "private")
    for actor, target, importance in [(owner, space, 1), (outsider, hidden, 5)]:
        proposal = layer.propose(actor["id"], target["id"], "项目数据库使用 SQLite", topic_key="storage", importance=importance)
        layer.approve(actor["id"], proposal["id"])
    result = layer.search(owner["id"], "项目使用什么存储引擎？", limit=1)
    assert len(result) == 1 and result[0]["space_id"] == space["id"]
    assert result[0]["retrieval"]["strategy"] == "acl_lexical_bm25"
