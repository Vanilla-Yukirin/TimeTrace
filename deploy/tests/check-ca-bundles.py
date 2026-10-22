"""Offline packaging check: run inside the built Debian runtime image."""

import importlib.metadata
import ssl
import subprocess

import certifi

certifi_version = importlib.metadata.version("certifi")
assert tuple(map(int, certifi_version.split("."))) >= (2026, 10, 19), certifi_version
system_version = subprocess.check_output(
    ["dpkg-query", "-W", "-f=${Version}", "ca-certificates"], text=True
).strip()
subprocess.run(["dpkg", "--compare-versions", system_version, "ge", "20250419~deb12u2"], check=True)
for bundle in ("/etc/ssl/certs/ca-certificates.crt", certifi.where()):
    context = ssl.create_default_context(cafile=bundle)
    assert context.cert_store_stats()["x509_ca"] > 0, bundle
    assert context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname
print(f"CA bundle checks passed: system={system_version}, certifi={certifi_version}")
