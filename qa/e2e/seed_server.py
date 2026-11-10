"""Seed only the disposable Compose home, then exec the production entry point."""

import os
from pathlib import Path

from timetrace.server.auth import ServerAuth, TokenEntry

assert Path.home() == Path("/home/timetrace")
if not ServerAuth.read_tokens():
    ServerAuth.write_tokens([TokenEntry(value="e2e-only-machine-token", label="e2e")])
os.execvp("timetrace-server", ["timetrace-server"])
