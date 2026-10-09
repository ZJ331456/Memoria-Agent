import asyncio
from pathlib import Path

from memoria.runtime.compaction import COMPACTION_PROMPT, SessionCompactor
from memoria.store import Store


class RecordingSummarizer:
    def __init__(self):
        self.calls: list[list[dict[str, str]]] = []

    async def complete(self, messages, model=None, max_tokens=None):
        self.calls.append(messages)
        text = messages[-1]["content"]
        anchor = "初始约束：只用 SQLite" if "初始约束：只用 SQLite" in text else "无约束"
        return f"## Goal\n{anchor}\n## Progress\n批次 {len(self.calls)}"


def _seed_messages(store: Store, session_id: str, count: int, first: str = "初始约束：只用 SQLite") -> list[str]:
    ids = []
    for index in range(count):
        saved = store.add_message(
            session_id, "user" if index % 2 == 0 else "assistant",
            first if index == 0 else f"后续消息 {index}",
        )
        ids.append(saved["id"])
    return ids


def test_compaction_walks_from_first_message_past_500_and_keeps_early_constraint(tmp_path: Path):
    store = Store(tmp_path / "long.db")
    session = store.create_session("long")
    ids = _seed_messages(store, session["id"], 620)
    other = store.create_session("other")
    foreign = _seed_messages(store, other["id"], 12, "其他会话秘密")
    summarizer = RecordingSummarizer()
    compactor = SessionCompactor(store, summarizer, max_batch_messages=80, max_batches=2)

    first = asyncio.run(compactor.ensure(session["id"], window=8))
    assert first and ids[0] in first.covered_message_ids
    assert "初始约束：只用 SQLite" in first.summary
    assert first.pending_messages > 0

    latest = first
    for _ in range(8):
        if latest.pending_messages == 0:
            break
        latest = asyncio.run(compactor.ensure(session["id"], window=8))
    assert latest and latest.pending_messages == 0
    assert latest.covered_message_ids == ids[:-8]
    assert not set(foreign) & set(latest.covered_message_ids)
    assert "初始约束：只用 SQLite" in latest.summary
    assert len(summarizer.calls) == 8
    store.close()


def test_partial_long_source_is_not_marked_covered_until_all_slices_are_seen(tmp_path: Path):
    path = tmp_path / "partial.db"
    store = Store(path)
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 12, first="X" * 5000)
    summarizer = RecordingSummarizer()
    compactor = SessionCompactor(store, summarizer, max_batches=1, max_input_chars=1024)

    first = asyncio.run(compactor.ensure(session["id"], window=4))
    assert first and first.partial_message_id == ids[0]
    assert first.partial_offset > 0
    assert ids[0] not in first.covered_message_ids
    assert store.session_compaction(session["id"])["partial_offset"] == first.partial_offset
    store.close()
    store = Store(path)
    compactor = SessionCompactor(store, summarizer, max_batches=1, max_input_chars=1024)

    latest = first
    for _ in range(30):
        if latest.pending_messages == 0:
            break
        latest = asyncio.run(compactor.ensure(session["id"], window=4))
    assert latest and latest.pending_messages == 0
    assert latest.covered_message_ids == ids[:-4]
    assert latest.partial_message_id is None
    assert sum(call[-1]["content"].count("X") for call in summarizer.calls) == 5000
    assert all(sum(len(item["content"]) for item in call) <= 1024 for call in summarizer.calls)
    store.close()


def test_same_batch_reuses_summary_without_another_model_call(tmp_path: Path):
    store = Store(tmp_path / "reuse.db")
    session = store.create_session()
    _seed_messages(store, session["id"], 12)
    summarizer = RecordingSummarizer()
    compactor = SessionCompactor(store, summarizer, max_batches=1)

    first = asyncio.run(compactor.ensure(session["id"], window=4))
    second = asyncio.run(compactor.ensure(session["id"], window=4))
    assert first and second and not first.reused and second.reused
    assert second.summary == first.summary
    assert len(summarizer.calls) == 1
    store.close()


def test_legacy_or_foreign_cursor_cannot_claim_another_sessions_sources(tmp_path: Path):
    store = Store(tmp_path / "stale.db")
    session = store.create_session("target")
    ids = _seed_messages(store, session["id"], 520)
    foreign_session = store.create_session("foreign")
    foreign = store.add_message(foreign_session["id"], "user", "其他会话秘密")
    store.upsert_session_compaction(session["id"], "错误旧摘要", ids[12:-4], ids[-5])
    summarizer = RecordingSummarizer()
    compactor = SessionCompactor(store, summarizer, max_batches=1)

    rebuilt = asyncio.run(compactor.ensure(session["id"], window=4))
    assert rebuilt and ids[0] in rebuilt.covered_message_ids
    assert "错误旧摘要" not in summarizer.calls[0][-1]["content"]
    assert "初始约束：只用 SQLite" in rebuilt.summary

    store.upsert_session_compaction(session["id"], "其他会话秘密", [foreign["id"]], foreign["id"])
    rebuilt_again = asyncio.run(compactor.ensure(session["id"], window=4))
    assert rebuilt_again and foreign["id"] not in rebuilt_again.covered_message_ids
    assert "其他会话秘密" not in summarizer.calls[-1][-1]["content"]
    store.close()


def test_deleting_source_session_cleans_raw_jobs_reviews_and_summary_but_keeps_memory(tmp_path: Path):
    store = Store(tmp_path / "deletion.db")
    session = store.create_session("source")
    ids = _seed_messages(store, session["id"], 12)
    asyncio.run(SessionCompactor(store, summarizer=None).ensure(session["id"], window=4))
    source = ids[0]
    job = store.enqueue_memory_job(source, "用户原话", "助手原话")
    store.claim_memory_job("worker")
    review = store.stage_memory_reviews(job["id"], "worker", [
        {"content": "用户喜欢 SQLite", "kind": "preference", "importance": 3}
    ])[0]
    memory = store.add_memory("用户喜欢 SQLite", "preference", source="reviewed_conversation", source_ref=source)
    assert store.session_compaction(session["id"])
    assert store.memory_job(job["id"])
    assert store.memory_review(review["id"])

    assert store.delete_session(session["id"])
    assert store.session_compaction(session["id"]) is None
    assert store.message_source(source) is None
    assert store.memory_job(job["id"]) is None
    assert store.memory_review(review["id"]) is None
    assert store.memory(memory["id"])["status"] == "active"
    assert store.db.execute("SELECT COUNT(*) FROM memory_operations WHERE source_ref=?", (source,)).fetchone()[0] == 1
    store.close()


def test_delete_session_does_not_commit_callers_transaction(tmp_path: Path):
    store = Store(tmp_path / "transaction.db")
    session = store.create_session()
    store.add_message(session["id"], "user", "保留")
    with store.lock:
        store.db.execute("BEGIN IMMEDIATE")
        store.upsert_session_compaction(session["id"], "待回滚摘要", [], None)
        assert store.delete_session(session["id"])
        store.db.execute("ROLLBACK")
    assert store.session(session["id"])
    assert store.session_compaction(session["id"]) is None
    assert store.messages(session["id"])[0]["content"] == "保留"
    store.close()


def test_no_model_compaction_keeps_progress_past_500_with_bounded_summary(tmp_path: Path):
    store = Store(tmp_path / "fallback-long.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 620)
    compactor = SessionCompactor(store, max_batches=2, max_summary_chars=6000)

    latest = None
    for _ in range(8):
        latest = asyncio.run(compactor.ensure(session["id"], window=8))
        assert latest and len(latest.summary) <= compactor.max_summary_chars
        if latest.pending_messages == 0:
            break
    assert latest and latest.pending_messages == 0
    assert latest.covered_message_ids == ids[:-8]
    assert "初始约束：只用 SQLite" in latest.summary
    assert "有损" in latest.summary
    store.close()


def test_oversized_model_summary_is_bounded_and_does_not_stall(tmp_path: Path):
    store = Store(tmp_path / "oversized-model.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 20)

    class OversizedSummarizer:
        async def complete(self, messages, model=None, max_tokens=None):
            return "## Goal\n" + "初始约束：只用 SQLite" + "甲" * 5000 + "\n## Key Decisions\n" + "乙" * 5000

    compactor = SessionCompactor(
        store, OversizedSummarizer(), max_batch_messages=3,
        max_batches=1, max_input_chars=1024, max_summary_chars=256,
    )
    latest = None
    for _ in range(8):
        latest = asyncio.run(compactor.ensure(session["id"], window=4))
        assert latest and len(latest.summary) <= compactor.max_summary_chars
        if latest.pending_messages == 0:
            break
    assert latest and latest.pending_messages == 0
    assert latest.covered_message_ids == ids[:-4]
    store.close()


def test_legacy_oversized_summary_is_bounded_before_new_batch_and_when_reused(tmp_path: Path):
    store = Store(tmp_path / "legacy-summary.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 14)
    legacy = "## Goal\n初始约束：只用 SQLite" + "甲" * 30000 + "\n## Key Decisions\n继续核查"
    store.upsert_session_compaction(session["id"], legacy, ids[:5], ids[4])
    summarizer = RecordingSummarizer()
    compactor = SessionCompactor(store, summarizer, max_batches=1)

    advanced = asyncio.run(compactor.ensure(session["id"], window=4))
    assert advanced and advanced.covered_message_ids == ids[:-4]
    assert len(advanced.summary) <= compactor.max_summary_chars
    assert len(COMPACTION_PROMPT) + len(summarizer.calls[0][-1]["content"]) <= compactor.max_input_chars
    assert "甲" * (compactor.max_summary_chars + 1) not in summarizer.calls[0][-1]["content"]

    store.upsert_session_compaction(session["id"], legacy, ids[:-4], ids[-5])
    reused_without_new_sources = asyncio.run(compactor.ensure(session["id"], window=4))
    assert reused_without_new_sources and not reused_without_new_sources.reused
    assert len(reused_without_new_sources.summary) <= compactor.max_summary_chars
    assert len(store.session_compaction(session["id"])["summary"]) <= compactor.max_summary_chars
    assert len(summarizer.calls) == 1
    store.close()


def test_minimum_summary_cap_still_allows_fallback_progress(tmp_path: Path):
    store = Store(tmp_path / "minimum-cap.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 20)
    compactor = SessionCompactor(
        store, max_summary_chars=192, max_input_chars=1024,
        max_batch_messages=3, max_batches=1,
    )

    latest = None
    for _ in range(8):
        latest = asyncio.run(compactor.ensure(session["id"], window=4))
        assert latest and len(latest.summary) <= compactor.max_summary_chars
        if latest.pending_messages == 0:
            break
    assert latest and latest.pending_messages == 0
    assert latest.covered_message_ids == ids[:-4]
    store.close()


def test_concurrent_ensure_does_not_overwrite_newer_cursor(tmp_path: Path):
    store = Store(tmp_path / "concurrent.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 12)

    class PausingSummarizer:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def complete(self, messages, model=None, max_tokens=None):
            self.calls += 1
            if self.calls == 2:
                self.started.set()
            await self.release.wait()
            return "## Goal\n初始约束：只用 SQLite\n## Key Decisions\n并发摘要"

    async def race():
        summarizer = PausingSummarizer()
        first = SessionCompactor(store, summarizer, max_batches=1)
        second = SessionCompactor(store, summarizer, max_batches=1)
        tasks = [
            asyncio.create_task(first.ensure(session["id"], 4)),
            asyncio.create_task(second.ensure(session["id"], 4)),
        ]
        await asyncio.wait_for(summarizer.started.wait(), timeout=2)
        summarizer.release.set()
        return await asyncio.gather(*tasks)

    results = asyncio.run(race())
    saved = store.session_compaction(session["id"])
    assert saved and saved["covered_message_ids"] == ids[:-4]
    assert all(result and result.covered_message_ids == ids[:-4] for result in results)
    assert sum(not result.reused for result in results) == 1
    store.close()


def test_deleted_source_with_few_remaining_messages_clears_persisted_summary(tmp_path: Path):
    store = Store(tmp_path / "source-cleared.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 8)
    assert asyncio.run(SessionCompactor(store, min_messages=4).ensure(session["id"], window=4))

    with store.lock:
        store.db.execute("DELETE FROM messages WHERE id=?", (ids[0],))
        store.db.commit()
    assert asyncio.run(SessionCompactor(store).ensure(session["id"], window=4)) is None
    assert store.session_compaction(session["id"]) is None
    store.close()


def test_deleted_session_during_summarizer_wait_leaves_db_usable(tmp_path: Path):
    store = Store(tmp_path / "deleted-during-await.db")
    session = store.create_session()
    _seed_messages(store, session["id"], 12)

    class PausingSummarizer:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def complete(self, messages, model=None, max_tokens=None):
            self.started.set()
            await self.release.wait()
            return "## Goal\n旧摘要"

    async def delete_while_waiting():
        summarizer = PausingSummarizer()
        task = asyncio.create_task(SessionCompactor(store, summarizer).ensure(session["id"], 4))
        await asyncio.wait_for(summarizer.started.wait(), timeout=2)
        assert store.delete_session(session["id"])
        summarizer.release.set()
        return await task

    assert asyncio.run(delete_while_waiting()) is None
    assert store.session_compaction(session["id"]) is None
    assert not store.db.in_transaction
    assert store.create_session("still works")
    store.close()


def test_source_deleted_during_summarizer_wait_is_not_persisted(tmp_path: Path):
    store = Store(tmp_path / "source-deleted-during-await.db")
    session = store.create_session()
    ids = _seed_messages(store, session["id"], 12, first="删除前敏感信息")

    class PausingSummarizer:
        def __init__(self):
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.calls = 0

        async def complete(self, messages, model=None, max_tokens=None):
            self.calls += 1
            text = messages[-1]["content"]
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
            return "## Goal\n" + ("删除前敏感信息" if "删除前敏感信息" in text else "保留后续信息")

    async def delete_while_waiting():
        summarizer = PausingSummarizer()
        task = asyncio.create_task(SessionCompactor(store, summarizer).ensure(session["id"], 4))
        await asyncio.wait_for(summarizer.started.wait(), timeout=2)
        with store.lock:
            store.db.execute("DELETE FROM messages WHERE id=?", (ids[0],))
            store.db.commit()
        summarizer.release.set()
        result = await task
        return result, summarizer.calls

    result, calls = asyncio.run(delete_while_waiting())
    assert result and calls == 2
    assert "删除前敏感信息" not in result.summary
    assert ids[0] not in result.covered_message_ids
    assert store.session_compaction(session["id"])["summary"] == result.summary
    store.close()
