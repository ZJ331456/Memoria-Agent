from __future__ import annotations

from contextvars import ContextVar, Token

current_session_id: ContextVar[str | None] = ContextVar("memoria_current_session_id", default=None)
current_turn_id: ContextVar[str | None] = ContextVar("memoria_current_turn_id", default=None)
current_request_id: ContextVar[str | None] = ContextVar("memoria_current_request_id", default=None)


class RequestContext:
    """Bind session/turn/request identities for logs and error correlation."""

    def __init__(self, *, session_id: str | None = None, turn_id: str | None = None, request_id: str | None = None):
        self.session_id = session_id
        self.turn_id = turn_id
        self.request_id = request_id
        self._tokens: list[tuple[ContextVar, Token]] = []

    def __enter__(self) -> "RequestContext":
        self._tokens = [
            (current_session_id, current_session_id.set(self.session_id)),
            (current_turn_id, current_turn_id.set(self.turn_id)),
            (current_request_id, current_request_id.set(self.request_id)),
        ]
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for var, token in reversed(self._tokens):
            var.reset(token)
        self._tokens.clear()


def snapshot() -> dict[str, str | None]:
    return {
        "session_id": current_session_id.get(),
        "turn_id": current_turn_id.get(),
        "request_id": current_request_id.get(),
    }
