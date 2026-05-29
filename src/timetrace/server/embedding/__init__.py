"""Text embedding client (OpenAI-compatible /v1/embeddings).

Single ``EmbeddingClient`` class — thin async wrapper around the OpenAI SDK
that returns the vector as packed float32 bytes ready for SQLite BLOB storage.
"""

from timetrace.server.embedding.client import EmbeddingClient, EmbeddingError

__all__ = ["EmbeddingClient", "EmbeddingError"]
