# Contributing

Open an issue before proposing a feature. Keep changes small and focused.
Use Windows and Python 3.12.7; see the README for setup commands.
Quit Gather before running tests: the full suite uses the clipboard and sends input.
Run `.\.venv\Scripts\python -m pytest -q` before submitting a change.
Run with `--no-desktop` if an interactive desktop is unavailable.
Add a regression test for a bug fix, using synthetic text and temporary files.
Keep copied text, personal paths, build outputs, and credentials out of commits.
Submit a pull request explaining the change and what you tested.
Report security issues through SECURITY.md.
