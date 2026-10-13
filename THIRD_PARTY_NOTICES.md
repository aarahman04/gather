# Third-party notices

Gather 1.0.0 is MIT licensed. Gather.exe also contains these components, which
retain their own copyrights and licenses. Package licenses were checked against
installed metadata and license files. Complete texts are in [docs/licenses](docs/licenses),
embedded in the EXE's extracted `licenses` directory, and supplied separately in
`Gather-licenses.zip` on each release.

## Python packages and bundler

| Component | Version | License and notice |
|---|---|---|
| pystray | 0.19.5 | LGPL-3.0-or-later; [GPL and LGPL texts](docs/licenses/pystray) |
| Pillow | 10.4.0 | HPND; [Pillow and native library notices](docs/licenses/Pillow/LICENSE) |
| python-docx | 1.1.2 | [MIT](docs/licenses/python-docx/LICENSE) |
| fpdf2 | 2.8.1 | [LGPL-3.0-or-later](docs/licenses/fpdf2/LICENSE) |
| defusedxml | 0.7.1 | [PSF license](docs/licenses/defusedxml/LICENSE) |
| fontTools | 4.66.1 | [MIT](docs/licenses/fonttools/LICENSE); Adobe glyph data is BSD-3-Clause and cu2qu is Apache-2.0 ([external notices](docs/licenses/fonttools/LICENSE.external)) |
| lxml | 6.1.3 | [BSD-3-Clause](docs/licenses/lxml/LICENSE.txt), with ElementTree/CPython-derived code and native libraries under [additional notices](docs/licenses/lxml/LICENSES.txt) |
| six | 1.17.0 | [MIT](docs/licenses/six/LICENSE) |
| typing_extensions | 4.16.0 | [PSF-2.0](docs/licenses/typing_extensions/LICENSE) |
| packaging | 26.3 | [Apache-2.0 or BSD-2-Clause](docs/licenses/packaging/LICENSE) |
| PyInstaller bootloader/runtime hooks | 6.10.0 | [GPL-2.0-or-later with bootloader exception; certain files Apache-2.0](docs/licenses/pyinstaller/COPYING.txt). The exception permits distributing Gather under its own license. |

Build/test tools (pytest, pip, setuptools, altgraph, pefile, pywin32-ctypes,
pyinstaller-hooks-contrib, pluggy, iniconfig and colorama) are pinned in
`requirements-dev.txt`; those packages are not included in the frozen runtime.
The bundle is checked against PyInstaller's analysis inventory.

## Native libraries and runtime

Pillow's Windows wheel includes FreeType 2.13.2 (the FreeType License is selected,
with credit to the FreeType Project), LittleCMS 2.16 (MIT), libwebp 1.4.0
(BSD-3-Clause), libjpeg-turbo 3.0.3 (IJG/BSD-3-Clause/zlib terms), OpenJPEG 2.5.2
(BSD-2-Clause), zlib 1.3.1 (zlib license), and libtiff 4.6.0 (libtiff's permissive
license). Its [installed composite license](docs/licenses/Pillow/LICENSE) also
covers Brotli 1.1.0 (MIT), HarfBuzz 8.5.0 (MIT), libpng 1.6.43 (PNG Reference
Library License), and liblzma from XZ 5.4.5 (public domain for the library).
It contains the complete notices. Raqm and libimagequant support
are absent from this wheel; those optional libraries are not bundled.
fontTools' test fonts, described in its external notice, are not in the EXE.

lxml's Windows wheel links libxml2 2.11.9, libxslt/libexslt 1.1.45 (MIT),
zlib 1.3.2 (zlib license), and libiconv 1.19 (LGPL-2.1-or-later, including
libcharset). See [lxml's installed wheel notices](docs/licenses/lxml/LICENSES.txt)
and [libiconv's license](docs/licenses/libiconv). Exact native source revisions
and Windows build scripts are included in the dependency source archive.

The EXE includes CPython 3.12.7 and its standard library under the
[PSF-2.0 and historical Python licenses](docs/licenses/Python/LICENSE.txt).
Included native components also include Tcl/Tk 8.6.13 (permissive Tcl/Tk licenses),
OpenSSL 3.0.15 (Apache-2.0), libffi 3.4.4 (MIT), bzip2 1.0.8 (bzip2 license),
liblzma from XZ 5.2.5 (public domain for the bundled library), zlib 1.3.1 (zlib),
Expat (MIT), libmpdec (BSD-2-Clause), and HACL* (MIT).
Python's installed license includes bzip2, libffi and Tcl/Tk terms; other native
texts were copied from matching runtime sources into [docs/licenses](docs/licenses).
Microsoft Visual C++ runtime DLLs retain Microsoft's redistribution terms,
reproduced in the Windows-binary section of Python's license.
The bundle also includes Microsoft UCRT/API compatibility DLLs under the same
Windows runtime terms. Other Windows system DLLs are loaded from the OS.
Windows fonts are read from the user's installation, not shipped in the EXE.

## Obtaining and replacing LGPL components

pystray, fpdf2 and lxml's statically linked libiconv are covered by LGPL terms.
The one-file EXE packages libraries internally, so replacement requires rebuilding
the EXE. Gather imposes no restriction on modifying these libraries or reverse
engineering the combined work to debug those modifications.

Download `Gather-dependency-sources.zip` beside Gather.exe on the
[release page](https://github.com/aarahman04/gather/releases/latest).
It includes unmodified pystray, fpdf2, lxml and libiconv sources, native dependency
source revisions and Windows native build scripts.
[resources/lgpl-sources.json](resources/lgpl-sources.json) lists original URLs and
SHA256 hashes. The tagged Gather source and build script are available from the
same release page. Keep these notices when redistributing the EXE.

To replace pystray or fpdf2, follow the README's source setup, then modify the
installed `pystray` or `fpdf` directory in `.venv\Lib\site-packages`, retaining
the package version metadata. Run `build.ps1` to package your modified library;
pip leaves an already installed matching version in place. Run the full tests.
For a fork with a different version, update the pinned requirement to that version
or use a local source requirement before building.

For libiconv, use the included native sources and the upstream
[Windows build instructions](https://github.com/lxml/libxml2-win-binaries/tree/2026.10.05).
Modify libiconv, rebuild the static libraries with Visual Studio, then rebuild
lxml from its included source, linking your modified libraries. See lxml's included
`setupinfo.py` and `buildlibxml.py` for static-library and include-directory options.
Install your lxml build into Gather's venv, retaining version 6.1.3, and rerun
`build.ps1` and the tests. Native library replacement needs compiler tools;
replacement of the Python libraries does not.
