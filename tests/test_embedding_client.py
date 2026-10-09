from __future__ import annotations

import asyncio
import json

import httpx

from memoria.config import ModelConfig
from memoria.memory.embedding import EmbeddingClient


def test_default_batches_preserve_all_texts_under_provider_limit(monkeypatch):
    batches = []

    def respond(request):
        texts = json.loads(request.content)["input"]
        batches.append(texts)
        if len(texts) > 10:
            return httpx.Response(400, json={"error": "batch exceeds ten"})
        # Providers may return vectors out of order.
        return httpx.Response(200, json={"data": [
            {"index": i, "embedding": [float(text.split("-")[1]), 1.0]}
            for i, text in reversed(list(enumerate(texts)))
        ]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr("memoria.memory.embedding.httpx.AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(respond), **kwargs))
    client = EmbeddingClient(ModelConfig(model="embedding-test", api_key="test-key", base_url="https://example.test/v1"))
    texts = [f"text-{i}" for i in range(23)]
    vectors = asyncio.run(client.embed_batch(texts))
    assert [len(batch) for batch in batches] == [10, 10, 3]
    assert [text for batch in batches for text in batch] == texts
    assert [vector[0] for vector in vectors] == list(range(23))
