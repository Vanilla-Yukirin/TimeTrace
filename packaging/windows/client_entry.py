"""Windowless PyInstaller entry point for the standalone TimeTrace client."""

from timetrace.client.windows_runtime import configure_windows_app_identity

configure_windows_app_identity()

from timetrace.client.cli import main  # noqa: E402

main()
