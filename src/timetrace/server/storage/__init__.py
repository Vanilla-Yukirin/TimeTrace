"""Blob storage tier: opaque-bytes Protocol + concrete implementations.

LocalBlobStorage writes under a configured root; S3BlobStorage will land at
P5. P3a's ingest endpoint will route screenshot uploads through this Protocol
instead of having the capture loop write the file directly, so the server is
the single point that decides where bytes live.
"""

from timetrace.server.storage.blob import BlobStorage, LocalBlobStorage

__all__ = ["BlobStorage", "LocalBlobStorage"]
