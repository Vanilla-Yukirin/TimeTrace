"""Render the same local snapshot used by the client control panel."""

import time

from prometheus_client import CollectorRegistry, Gauge, generate_latest


def render_client_metrics(snapshot) -> bytes:
    registry = CollectorRegistry()
    values = {
        "outbox_pending_entries": (
            snapshot.outbox_pending,
            "Entries awaiting upload acknowledgement",
        ),
        "outbox_pending_bytes": (snapshot.outbox_bytes, "Bytes awaiting upload acknowledgement"),
        "outbox_oldest_pending_seconds": (
            max(0, time.time() - snapshot.outbox_oldest_at) if snapshot.outbox_oldest_at else 0,
            "Age of oldest pending entry",
        ),
        "last_capture_timestamp_seconds": (snapshot.last_capture_at or 0, "Last durable capture"),
        "last_upload_timestamp_seconds": (snapshot.last_upload_at or 0, "Last successful upload"),
        "paused": (snapshot.paused, "Capture is paused"),
        "last_error_timestamp_seconds": (snapshot.last_error_at or 0, "Last observed client error"),
    }
    for name, (value, help_text) in values.items():
        Gauge("timetrace_client_" + name, help_text, registry=registry).set(value)
    return generate_latest(registry)
