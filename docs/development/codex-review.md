# Gather independent review

Historical review of an early build. Findings and test counts below describe that
build, not the current release. Consult current tests before treating a finding as open.

Reviewed `98da13e` (main), 2026-10-13, Windows 11, Python 3.12.7.
Read README and every file in `gather/`. Source unchanged; tests and this report are on
`codex/review`. Suggested diffs below are sketches, **not applied**.

## Findings

No critical finding. Two high, six medium, two low findings were reproduced.

### F1 — High: a failed export discards the session

`gather/__main__.py:130` clears `self.session` before `writers.write` at line 135.
A denied Documents directory, full disk, or writer exception leaves no retryable
session. `drain` does notify the user; the content is nevertheless lost. Reproduced
with a `PermissionError`. Restore recording state if export fails:

```diff
-        path = writers.write(session, self.cfg.format, self.folder, self.cfg.mask, ended)
+        try:
+            path = writers.write(session, self.cfg.format, self.folder, self.cfg.mask, ended)
+        except Exception:
+            self.session = session
+            self.watcher.set_capturing(True)
+            self.refresh()
+            self.pill.show(len(session))
+            raise
```

### F2 — High: window-title secrets bypass masking

`gather/writers.py:23` inserts an unmasked title in every export; line 32 masks only
chunk text. A terminal/editor title containing a supported `sk-…` key exposes it
even with masking enabled. Reproduced for MD, DOCX, and text passed to the PDF writer.
Mask metadata before truncation, and pass `mask` at all three `_label` call sites:

```diff
-def _label(i: int, c: Chunk) -> str:
+def _label(i: int, c: Chunk, mask: bool) -> str:
+    title = mask_secrets(c.title) if mask else c.title
-    title = c.title if len(c.title) <= 60 else c.title[:59] + "…"
+    title = title if len(title) <= 60 else title[:59] + "…"
-        doc.add_heading(_label(i, c), 2)
+        doc.add_heading(_label(i, c, mask), 2)
```

Also mask the app label. Apply the same argument change in `render_markdown` and
`_pdf`; retaining the flag preserves the user's masking-off behavior.

### F3 — Medium: odd titles crash MD/DOCX export

`gather/writers.py:27`, `:51`, `:117`: surrogate replacement and XML cleanup apply
to body text, not metadata. A title with a lone surrogate raises `UnicodeEncodeError`
in MD/DOCX; a title with U+0001 raises `ValueError` in DOCX. F1 then loses the session.
Sanitize the assembled label in the shared helper:

```diff
     who = " — ".join(p for p in (app, title) if p) or "unknown"
+    who = _ILLEGAL.sub("", who.encode("utf-8", "replace").decode())
```

### F4 — Medium: a 500 ms clipboard lock defeats attachment

`gather/win32.py:117`: ten 20 ms retries expire after about 200 ms. A subprocess
holding `OpenClipboard` with a real HWND for 500 ms makes both clipboard writers
raise WinError 5. In `App.finish`, the file already exists, but CF_HDROP is not set;
the error notification does not identify the saved file. Extend the bounded retry
window and include the existing path in attachment-failure feedback:

```diff
-    for _ in range(10):
+    for _ in range(50):  # allow a transient lock to clear, up to one second
```

### F5 — Medium: an ownership race captures Gather's own text

`gather/win32.py:313`: owner is checked before metadata lookup and clipboard opening.
If Tk executes “Copy last as text/path” between that check and the read, the watcher
collects Gather's own write. Deterministic interleaving reproduced this. Check owner
while reading, and serialize in-process clipboard reads/writes with a shared lock:

```diff
     _open_clipboard(hwnd)
     try:
+        owner = user32.GetClipboardOwner()
+        if skip_excluded and owner and _window_pid(owner) == os.getpid():
+            return None
```

The extra check belongs under the shared lock with all three clipboard operations;
the early owner check can remain as a fast path.

### F6 — Medium: tray settings race with export

`gather/ui.py:132`, `:137` mutate config directly on the tray thread, contrary to
`gather/__main__.py:3`. `finish` reads `mask` independently at lines 135 and 136.
Toggling masking during export can produce a masked file and an unmasked “Copy last
as text”. Reproduced by interleaving the mutation at the writer boundary. Snapshot
both settings for the export and route settings changes through the Tk event queue:

```diff
     def finish(self) -> None:
+        fmt, mask = self.cfg.format, self.cfg.mask
-        path = writers.write(session, self.cfg.format, self.folder, self.cfg.mask, ended)
-        self.last_text = writers.render_markdown(session, self.cfg.mask, ended)
+        path = writers.write(session, fmt, self.folder, mask, ended)
+        self.last_text = writers.render_markdown(session, mask, ended)
```

### F7 — Medium: delayed metadata lookup loses copies

`gather/win32.py:316`: metadata is queried synchronously before reading text.
The normal 50-copy/2-second test passed. With the first foreground query paused,
six copies yielded only the final copy; Windows updates carry no historical text.
Keep the listener limited to reading and enqueueing immutable text, and resolve
metadata separately using the HWND captured with that text:

```diff
-        app, title = _foreground_info()
         # read clipboard text here
         if text:
-            self._on_text(text, app, title)
+            self._pending.put((text, user32.GetForegroundWindow()))
```

This sketch requires initializing and draining that queue and resolving the saved
HWND. It reduces watcher stalls; it cannot guarantee recovery of clipboard states
already overwritten before Windows schedules the listener.

### F10 — Medium: a 5 MB PDF export stalls the UI

`gather/writers.py:89`, `gather/__main__.py:135`: exporting a 5 MiB unbroken text
chunk to PDF did not finish within **120 seconds**; the child was killed at the
timeout. MD completed in 0.65 s and DOCX in 1.53 s. `finish` runs the same synchronous
writer on Tk's thread, so this workload prevents processing hotkeys and Quit.
Move exports to one worker, returning completion/errors through the Tk queue:

```diff
-        path = writers.write(session, fmt, self.folder, mask, ended)
+        future = self.exports.submit(writers.write, session, fmt, self.folder, mask, ended)
+        future.add_done_callback(lambda done: self.q.put(("export_done", done)))
```

Initialize a single-worker executor, freeze the session/settings, and move the
remaining completion work to `on_export_done`, where `future.result()` errors reach
`drain`. Quit must await pending exports rather than discard them. This addresses
UI responsiveness; PDF completion time still requires profiling. The regression
uses a shorter 30 s timeout after the initial 120 s reproduction.

### F8 — Low: a public sk-learn slug is masked

`gather/session.py:38`: `sk-learn-feature-selection-guide` becomes `[masked]`.
Git SHAs, UUIDs, SHA256, ordinary base64, `task-…`, and plain `sk-learn` all passed.
Exclude this known public prefix or tighten token recognition with provider-specific
fixtures. A narrow fix for the reproduced false positive is:

```diff
-    r"|\bsk-(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
+    r"|\bsk-(?!learn(?:-|\b))(?:ant-|proj-)?[A-Za-z0-9_-]{20,}"
```

Validate against real provider formats before adoption; the exclusion introduces
a deliberate ambiguity for any actual key starting with `sk-learn-`.

### F9 — Low: invalid text empties the old clipboard

`gather/win32.py:346`: a lone surrogate triggers encoding failure after
`EmptyClipboard`, destroying previous clipboard contents. Both text and file-list
writers should finish encoding/validation before opening and emptying the clipboard:

```diff
 def copy_text_to_clipboard(text: str) -> None:
+    data = _text_bytes(text)
     _open_clipboard(_owner_hwnd)
     try:
         user32.EmptyClipboard()
-        _set_global(CF_UNICODETEXT, _text_bytes(text))
+        _set_global(CF_UNICODETEXT, data)
```

## Tests and results

- Baseline, before adversarial tests: **41 passed**.
- Added **72 pytest cases** in `tests/codex/test_adversarial.py`: **57 passed,
  15 strict xfailed**. The xfails preserve reproduced defects without changing source;
  an unexpected pass fails the suite and means that expectation needs updating.
- Full suite run by `build.ps1`: **98 passed, 15 xfailed**, 68.57 seconds.
- `build.ps1`: succeeded, `dist/Gather.exe` produced/validated by PyInstaller.
- `tests/codex/exe_check.py`: passed. Started the real EXE with isolated APPDATA;
  sent Ctrl+Alt+G using `SendInput` after verifying foreground HWND/title
  (WindowsTerminal.exe); copied two synthetic strings using PowerShell `Set-Clipboard`;
  verified ordered content, two items, a new Documents\Gather MD file, and the exact
  CF_HDROP path. Terminated the verified test-owned EXE PID tree and deleted the file.
- `live_safety.py` and `conftest.py`: clipboard text saved/restored and equality
  verified around pytest and the EXE run. No website uploads or real-chat pastes.

Added coverage:

| Tests | Result |
| --- | --- |
| `test_5mb_clipboard_is_captured_exactly`, `test_50_copies_in_two_seconds_keep_order` | 2 passed |
| `test_5mb_writes_every_format` | MD/DOCX passed; PDF timed out (F10) |
| `test_nontext_is_ignored` (valid 1x1 CF_DIB, CF_HDROP) | 2 passed |
| `test_unicode_clipboard_capture` (surrogates, RTL, zero-width, CRLF, emoji/CJK) | 5 passed |
| `test_tricky_input_writes_every_format` (7 inputs × MD/DOCX/PDF) | 21 passed; DOCX reopened, PDF signature checked |
| `test_masking_table`, `test_public_sk_learn_slug_is_not_masked` | 15 passed, 1 xfailed (F8) |
| `test_sequential_filename_collisions_preserve_files` | 3 passed |
| `test_global_memory_failure_frees_owned_handle_and_closes` | 3 passed: allocation, lock, SetClipboardData failures |
| `test_quit_saves_nonempty_session` | 1 passed with UI/clipboard doubles |
| `test_illegal_title_does_not_break_export` | 3 passed, 3 xfailed (F3) |
| `test_masking_also_covers_window_titles` | 3 xfailed (F2) |
| `test_write_waits_for_500ms_clipboard_lock` | 2 xfailed (F4) |
| Export retry, config interleaving, owner race, encoding preservation, delayed copies | 5 xfailed (F1/F6/F5/F9/F7) |

Reproduce locally (these commands touch the live clipboard; the plugin guards
the existing hotkey test as well as restoring text):

```powershell
$env:PYTHONPATH = (Resolve-Path tests\codex).Path
$env:PYTEST_PLUGINS = 'live_safety'
$env:PYTHONIOENCODING = 'utf-8'
.\.venv\Scripts\python -m pytest -q
.\build.ps1
.\.venv\Scripts\python tests\codex\exe_check.py
```

## Limits and other review observations

No 64-bit handle truncation was found in the exercised Win32 paths. Clipboard
read/write paths use `finally` to close; the injected allocation/lock/set failures
freed untransferred memory. These checks do not prove absence of every native leak.
UI `SystemParametersInfoW` and the entry-point MessageBox/DPI calls lack explicit
signatures, but their current arguments did not reproduce a type failure.

Quit saves a nonempty session in the unit check. Unexpected process death has no
recovery journal (`session.py:18`, `__main__.py:63`); in-memory recording is lost.
I did not deliberately crash a recording EXE or exhaust real disk space. The disk
failure test uses injection; the race and slow-metadata cases use deterministic
interleavings rather than hoping for a timing accident. Same-minute sequential
collisions passed; concurrent cross-process writers were not tested (filename
selection uses exists-then-write at `writers.py:114`).

Not tested: actual tray Quit interaction, Explorer/browser pasting, PDF visual
fidelity or extracted text, successful completion of the 5 MB PDF, malformed unterminated native
clipboard buffers, all provider key families, autostart after reboot, or other
Windows/font configurations. PDF title masking was checked at the actual
`FPDF.multi_cell` input. Restoration covers clipboard **text**, not original image,
file-list, or other rich formats.
