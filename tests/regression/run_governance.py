"""Run the synthetic governance benchmark against the real local implementation."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from memoria.governance import GovernanceError, MemoryGovernance
from memoria.store import Store

from .governance_eval import DEFAULT_CASES, evaluate_governance


# Each case gets a fresh database. These fixed times model whether a fixture
# memory has expired at that case's as_of, without relying on today's date.
PAST = "2000-01-01T00:00:00+00:00"
FUTURE = "2999-01-01T00:00:00+00:00"


def _instant(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _build_case(fixture: dict[str, Any], case: dict[str, Any], store: Store) -> tuple[MemoryGovernance, dict[str, Any]]:
    governance = MemoryGovernance(store)
    agents = {item["id"]: governance.create_agent(item["id"])["id"] for item in fixture["agents"]}
    spaces = {
        "private-alice": governance.create_space(agents["agent-alice"], "Alice private", "private")["id"],
        "team-alpha": governance.create_space(agents["agent-alice"], "Alpha", "shared")["id"],
        "team-beta": governance.create_space(agents["agent-carol"], "Beta", "shared")["id"],
    }
    governance.grant(agents["agent-alice"], spaces["team-alpha"], agents["agent-bob"], "contributor")

    session_ids: dict[str, str] = {}
    message_ids: dict[str, str] = {}
    for message in fixture["source_messages"]:
        label = message["session_id"]
        if label not in session_ids:
            session_ids[label] = store.create_session(label)["id"]
        message_ids[message["id"]] = store.add_message(session_ids[label], "user", message["content"])["id"]

    as_of = _instant(case["as_of"])
    memory_ids: dict[str, str] = {}
    active_memories = sorted(
        (item for item in fixture["memories"] if _instant(item["created_at"]) <= as_of),
        key=lambda item: (item["created_at"], item["id"]),
    )
    for item in active_memories:
        space_id = spaces["private-alice"] if item["scope"] == "private" else spaces[item["team_id"]]
        owner = agents[item["owner_agent_id"]]
        expiry = item.get("expires_at")
        expired = bool(expiry and _instant(expiry) <= as_of)
        # Approval refuses already-expired proposals, so first approve an
        # unexpired fixture and then set its clock-relative expiry.
        proposal = governance.propose(
            owner, space_id, item["content"], topic_key=item["topic_key"],
            source_type="conversation", source_ref=message_ids[item["source_message_id"]],
            expires_at=FUTURE if expiry else None,
        )
        replaces_id = memory_ids.get("m-alpha-old-endpoint") if item["id"] == "m-alpha-new-endpoint" else None
        space_owner = agents["agent-carol"] if item["team_id"] == "team-beta" else agents["agent-alice"]
        saved = governance.approve(space_owner, proposal["id"], reason="benchmark fixture", expected_replaces_id=replaces_id)
        memory_ids[item["id"]] = saved["id"]
        if expired:
            with store.lock:
                store.db.execute("UPDATE governed_memories SET expires_at=? WHERE id=?", (PAST, saved["id"]))
                store.db.execute("UPDATE proposals SET expires_at=? WHERE id=?", (PAST, proposal["id"]))
                store.db.commit()

    for item in active_memories:
        revoked_at = item.get("revoked_at")
        if revoked_at and _instant(revoked_at) <= as_of:
            space_owner = agents["agent-carol"] if item["team_id"] == "team-beta" else agents["agent-alice"]
            governance.revoke_memory(space_owner, memory_ids[item["id"]], reason="benchmark fixture revocation")

    for action in case.get("grant_actions", []):
        space_id = spaces[action["team_id"]]
        space_owner = agents["agent-carol"] if action["team_id"] == "team-beta" else agents["agent-alice"]
        target = agents[action["agent_id"]]
        if action["action"] == "grant":
            governance.grant(space_owner, space_id, target, action["role"])
        elif action["action"] == "revoke":
            governance.revoke_grant(space_owner, space_id, target)
        else:
            raise ValueError(f"unknown grant action: {action['action']}")

    mapping = {
        "agents": agents,
        "spaces": spaces,
        "memory_ids": memory_ids,
        "memory_labels": {value: key for key, value in memory_ids.items()},
        "message_ids": message_ids,
        "message_labels": {value: key for key, value in message_ids.items()},
        "session_labels": {value: key for key, value in session_ids.items()},
    }
    return governance, mapping


def _predict_case(
    fixture: dict[str, Any], case: dict[str, Any], store: Store
) -> dict[str, Any]:
    governance, mapping = _build_case(fixture, case, store)
    actor_id = mapping["agents"][case["actor_id"]]
    if case["operation"] == "propose":
        candidate = case["candidate"]
        space_id = mapping["spaces"]["private-alice"] if candidate["scope"] == "private" else mapping["spaces"][candidate["team_id"]]
        try:
            proposal = governance.propose(
                actor_id, space_id, candidate["content"], topic_key=candidate["topic_key"],
                source_type="conversation", source_ref=mapping["message_ids"][candidate["source_message_id"]],
            )
        except GovernanceError as exc:
            if exc.status_code == 403:
                return {"decision": "reject"}
            raise
        return {"decision": "needs_review" if proposal.get("conflict_memory_id") else "eligible"}

    if case["operation"] == "inspect":
        memory_id = mapping["memory_ids"][case["memory_id"]]
        try:
            records = [governance.detail(actor_id, memory_id)]
        except GovernanceError as exc:
            if exc.status_code == 403:
                records = []
            else:
                raise
    else:
        records = governance.search(actor_id, case["query"], limit=100)
    answer: dict[str, Any] = {"memory_ids": [], "sources": []}
    for record in records:
        memory_id = record["id"]
        answer["memory_ids"].append(mapping["memory_labels"].get(memory_id, memory_id))
        # Resolve provenance through the authorization-checked detail method.
        detail = governance.detail(actor_id, memory_id)
        source_ref = detail.get("source_ref")
        if detail.get("source_type") not in {"conversation", "reviewed_conversation"} or not source_ref:
            continue
        source = store.message_source(source_ref)
        if source:
            answer["sources"].append({
                "memory_id": mapping["memory_labels"].get(memory_id, memory_id),
                "message_id": mapping["message_labels"].get(source_ref, source_ref),
                "session_id": mapping["session_labels"].get(source["session_id"], source["session_id"]),
            })
    return answer


def run(fixture: dict[str, Any], k: int = 5) -> dict[str, Any]:
    """Seed isolated stores, query MemoryGovernance and score actual outputs."""
    predictions: dict[str, dict[str, Any]] = {}
    with tempfile.TemporaryDirectory(prefix="memoria-governance-eval-") as directory:
        for case in fixture["cases"]:
            store = Store(Path(directory) / f"{case['id']}.db", "json")
            try:
                predictions[case["id"]] = _predict_case(fixture, case, store)
            finally:
                store.close()
    report = evaluate_governance(fixture, predictions, k)
    return {"report": asdict(report), "predictions": predictions}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run governance benchmark against local MemoryGovernance")
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--predictions-out", type=Path, help="Save actual system outputs for independent scoring")
    parser.add_argument("-k", type=int, default=5)
    parser.add_argument("--min-pass-rate", type=float, help="Exit with status 1 below this case pass rate")
    parser.add_argument("--max-leak-rate", type=float, help="Exit with status 1 above this isolation leak rate")
    args = parser.parse_args()
    fixture = json.loads(args.cases.read_text(encoding="utf-8"))
    result = run(fixture, args.k)
    if args.predictions_out:
        args.predictions_out.write_text(json.dumps(result["predictions"], ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result["report"], ensure_ascii=False, indent=2))
    report = result["report"]
    if args.min_pass_rate is not None and report["case_pass_rate"] < args.min_pass_rate:
        raise SystemExit(1)
    if args.max_leak_rate is not None and report["isolation_leak_rate"] > args.max_leak_rate:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
