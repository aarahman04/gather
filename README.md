# Gather

Copy as many things as you like, from any app, then paste them all as **one file**.

**Click your mouse wheel** (middle-click), then copy from your browser, terminal,
editor, chat, anything. Middle-click again. Gather writes everything into one Markdown file and puts
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
Security software may also flag programs that use global mouse hooks.

Windows 11 hides new tray icons under the **^** arrow. Drag the Gather icon onto the
taskbar if you want it always visible. You don't need it, though: a small red
**● Gather N** pill shows at the top of the screen whenever it's recording.

## Use

| | |
|---|---|
| Start / stop recording | **Middle-click** (default), **Alt+C**, or left-click the tray icon |
| While recording | every text copy is collected; the pill counts items and briefly previews each copy |
| Remove a wrong copy | **Alt+Z**, or tray → **Remove last item (Alt+Z)**; repeat to remove earlier items |
| After stopping | the file is on your clipboard. Press **Ctrl+V** wherever you want it |
| While saving | the pill shows a grey **Saving…** (large PDFs take a few seconds); you can start a new session meanwhile |

You always know the state: grey round tray icon = idle; red square icon, the pill
at the top of the screen, and a short sound = recording. A notification names the file
when you stop.

The pill shows the first 40 characters of a copy for about two seconds, with line
breaks shown as spaces and **…** when shortened. Undo shows **Removed: <preview>**;
an empty session shows **Nothing to undo**. Previews always mask recognized secrets
using the same masker as exports, even when export masking is switched off. The pill
stays out of your way: it does not take focus and clicks pass through it.

**Alt+Z belongs to Gather only while recording.** It removes the most recent item
without changing your clipboard; there is no redo. If another app owns Alt+Z, Gather
notifies you and the tray action still works. Undo is recorded in the crash journal,
so recovery does not restore removed items. Copying a removed item again collects it
again, and undo also works if a failed save resumes recording. If the undo record
cannot be written, Gather keeps the item and tells you to retry.

**The default replaces normal middle-click in every app:** opening links in new tabs,
closing tabs and autoscroll will stop working while Gather is running. This is intentional.
To keep those actions, choose tray → **Mouse trigger → Alt + middle-click**;
plain middle-click then works normally. Choose **Off** to remove the mouse hook entirely.
Gather consumes the trigger's press and release and toggles once on the press.
For Alt + middle-click, it sends an unassigned key (VK_E8) to prevent releasing Alt
from activating the app's menu bar; it does not release your Alt key.

**Where paste works**

- **File Explorer / desktop:** creates the file.
- **Browser chats (claude.ai, ChatGPT, …):** attaches the file.
- **Editors** that accept dropped files (e.g. VS Code explorer): add the file.
- **Windows Terminal:** pastes the file's full path. For the text itself, use tray →
  **Copy last as text**.

**Copying from terminals.** Gather collects whatever lands on the clipboard. The pill count
goes up on every capture, so if it doesn't move, the terminal didn't copy. Tested on
Windows 11:

- **Windows Terminal:** select with the mouse, then **Ctrl+C**, **Ctrl+Shift+C** or
  right-click. Ctrl+C copies only while the selection is still highlighted. If the
  highlight is gone (for example because output kept scrolling), Ctrl+C goes to the
  running program, and in Claude Code or a shell that means *interrupt*, not copy.
- **Claude Code:** when Claude Code handles the mouse itself, just selecting text copies
  it (you'll see "Copied N characters"); don't press Ctrl+C afterwards. To use Windows
  Terminal's own selection instead, hold **Shift** while dragging, then press Ctrl+Shift+C.
- **PowerShell in the classic console window** (not Windows Terminal): Ctrl+A and
  Ctrl+C act on the command line only. Select with the mouse and press **Enter** or
  right-click.
- **cmd:** select with the mouse (or Ctrl+A), then **Enter** or Ctrl+C.

**Tray menu**

- *Remove last item (Alt+Z)* / *Cancel session* (while recording)
- *Copy last as text*: the last session as plain Markdown text
- *Copy last file path*
- *Open Gather folder*: every session is saved in `Documents\Gather`, which doubles as your history
- *Format*: Markdown (default), Word, or PDF
- *Hotkey*: Alt+C (default), Win+Y, Pause, Scroll Lock, or Ctrl+Alt+G. Changes apply immediately and are saved in your config. If a key is taken by another app, Gather says so and keeps the previous working key; left-clicking the tray still works
- *Mouse trigger*: Middle-click (default; replaces normal middle-click), Alt + middle-click, or Off. Changes apply immediately and are saved. A change during a held trigger waits for its release; if it stays held, retry after releasing it. If the hook cannot be installed, Gather switches this setting to Off and notifies you; keyboard and tray toggles keep working
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
.\.venv\Scripts\python -m pytest -q        # tests send keyboard/mouse input and use the clipboard
.\build.ps1                                 # -> dist\Gather.exe
```

Python 3.12, Windows 10/11. Code: `gather/win32.py` (clipboard, hotkey, mouse hook; ctypes only),
`session.py`, `writers.py`, `ui.py` (tray, pill), `__main__.py` (wiring).
