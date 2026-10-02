"""Legacy background writes must not commit another service's transaction."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from threading import Event

import pytest

from memoria.governance import MemoryGovernance
from memoria.memory import MemoryEngine
from memoria.store import Store


@pytest.mark.parametrize("supersede", [False, True])
def test_legacy_metadata_write_preserves_governance_rollback(tmp_path, monkeypatch, supersede):
    store = Store(tmp_path / "transactions.db", vector_backend="json")
    governance = MemoryGovernance(store)
    owner = governance.create_agent("original owner")
    if supersede:
        store.add_memory("用户喜欢喝绿茶", "preference")

    async def decide(_content, _kind, candidates):
        return {"action": "supersede", "target_id": candidates[0]["id"]}

    engine = MemoryEngine(store, decider=decide if supersede else None)
    organized = Event()
    resume = Event()
    original_organize = engine.organize

    def pause_before_metadata(content, kind):
        result = original_organize(content, kind)
        organized.set()
        assert resume.wait(5)
        return result

    monkeypatch.setattr(engine, "organize", pause_before_metadata)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                asyncio.run, engine.remember("用户喜欢喝绿茶饮品", "preference", 3, "manual")
            )
            try:
                assert organized.wait(5)
                with pytest.raises(RuntimeError, match="abort approval"):
                    with governance._transaction():
                        store.db.execute("UPDATE agents SET name='uncommitted owner' WHERE id=?", (owner["id"],))
                        resume.set()
                        # The legacy writer must wait until the governance transaction ends.
                        with pytest.raises(TimeoutError):
                            future.result(timeout=0.2)
                        raise RuntimeError("abort approval")
            finally:
                resume.set()
            result = future.result(timeout=5)
        assert governance.list_agents()[0]["name"] == "original owner"
        assert result.action == ("superseded" if supersede else "created")
        assert json.loads(store.memory(result.memory["id"])["entities_json"])
    finally:
        store.close()
