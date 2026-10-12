# Gather

Copy as many things as you like, from any app, then paste them all as **one file**.

Press **Ctrl+Alt+G**, copy from your browser, terminal, editor, chat, anything.
Press **Ctrl+Alt+G** again. Gather writes everything into one Markdown file and puts
that *file* on your clipboard, so **Ctrl+V** in a folder, an editor or a chat's upload
box pastes the file itself.

Built for moving AI-agent output between tools without the copy → notebook → save →
upload routine.

## Install

1. Download `Gather.exe` from the [latest release](https://github.com/aarahman04/gather/releases/latest).
2. Put it anywhere (e.g. `C:\Tools\Gather.exe`) and double-click it. Nothing is installed.
3. Optional: right-click the tray icon → **Start with Windows**.

**SmartScreen:** Gather is not code-signed, so the first launch of a downloaded copy may
show "Windows protected your PC". Click **More info → Run anyway**. Windows Defender
reports no threats on the release build.

Windows 11 hides new tray icons under the **^** arrow. Drag the Gather icon onto the
taskbar if you want it always visible. You don't need it, though: a small red
**● Gather N** pill shows at the top of the screen whenever it's recording.

## Use

| | |
|---|---|
| Start / stop recording | **Ctrl+Alt+G**, or left-click the tray icon |
| While recording | every text copy is collected; the pill counts items |
| After stopping | the file is on your clipboard. Press **Ctrl+V** wherever you want it |
| While saving | the pill shows a grey **Saving…** (large PDFs take a few seconds); you can start a new session meanwhile |

You always know the state: grey round tray icon = idle; red square icon, the pill
at the top of the screen, and a short sound = recording. A notification names the file
when you stop.

**Where paste works**

- **File Explorer / desktop:** creates the file.
- **Browser chats (claude.ai, ChatGPT, …):** attaches the file.
- **Editors** that accept dropped files (e.g. VS Code explorer): add the file.
- **Windows Terminal:** pastes the file's full path. For the text itself, use tray →
  **Copy last as text**.

**Tray menu**

- *Remove last item* / *Cancel session* (while recording)
- *Copy last as text*: the last session as plain Markdown text
- *Copy last file path*
- *Open Gather folder*: every session is saved in `Documents\Gather`, which doubles as your history
- *Format*: Markdown (default), Word, or PDF
- *Mask secrets* (on by default): replaces things that are almost certainly API keys
  (`sk-…`, `ghp_…`, `AKIA…`, `xoxb-…`, `AIza…`, private-key blocks) with `[masked]`,
  in the copied text and in window titles
- *Start with Windows*, *Quit*

**What goes in the file**

```markdown
# Gather — 2026-10-13 14:30–14:45 · 3 items

## 1 · chrome — Claude · 14:31

<what you copied, verbatim>

---

## 2 · WindowsTerminal — pwsh · 14:33
...
```

Gather keeps the copied text exactly as it was copied. It skips a copy that is the same
as the one before it (apps often fire twice), ignores non-text copies (images, files),
and ignores copies that password managers mark as private. Settings live in
`%APPDATA%\Gather\config.json`.

**Nothing gets lost.** While recording, each copy is also appended to a small file in
`%APPDATA%\Gather\unsaved\` (raw, unmasked text, deleted once the session's file is saved).
If Gather crashes or is killed, the next start saves that session to `Documents\Gather`
and tells you (it doesn't touch your clipboard). If saving fails (disk full, folder locked),
Gather keeps recording the same session: fix the cause or switch format, then stop again.

## Run from source / build

```powershell
git clone https://github.com/aarahman04/gather.git; cd gather
python -m venv .venv; .\.venv\Scripts\pip install -r requirements-dev.txt
.\.venv\Scripts\pythonw -m gather          # run
.\.venv\Scripts\python -m pytest -q        # tests (they press Ctrl+Alt+G and use the clipboard)
.\build.ps1                                 # -> dist\Gather.exe
```

Python 3.12, Windows 10/11. Code: `gather/win32.py` (clipboard, hotkey; ctypes only),
`session.py`, `writers.py`, `ui.py` (tray, pill), `__main__.py` (wiring).
