# Development notes

The application is wired in `gather/__main__.py`. `win32.py` handles clipboard,
hotkeys and mouse hooks; `session.py` holds sessions and recovery journals;
`writers.py` exports files; `ui.py` provides the tray and recording overlay.

- [Historical review](codex-review.md): findings from an early pre-release build.
- Desktop tests are marked `desktop` in the tests. CI runs `pytest -q --no-desktop`.
  This skips real clipboard, keyboard/mouse input, hotkey/hook integration and Tk
  overlay tests. Session, journal, export, configuration and mocked integration
  tests still run. Run `pytest -q` on an interactive Windows desktop before a release.

The release workflow calls `build.ps1 -NoDesktop` with Python 3.12.7 and the pinned
requirements. It publishes the EXE, SHA256, licenses and LGPL dependency sources.
Local and CI EXEs may differ because paths, PE timestamps and OS/runtime files differ;
the workflow-built EXE is the official release. The checksum verifies that asset.
