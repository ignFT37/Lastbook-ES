# Lastbook ES: building the Windows app and its release

`LastbookES.exe` is Lastbook's Windows app built from this tree. It sits beside the official Lastbook without sharing its settings
(`%APPDATA%\LastbookES`) or its library (`%LOCALAPPDATA%\LastbookES\LastbookData`). On first run it downloads what it needs:

| What | From | Pinned by |
|---|---|---|
| Small library (7.2 GB) | the official download site | size and SHA-256 in the manifest the app carries |
| One community's map (50 MB to 1.9 GB) | assets of this fork's GitHub release | size and SHA-256 in the same manifest |
| Local AI, Gemma 4 E4B (4.8 GB), optional | Hugging Face, at a fixed revision | size and SHA-256 |

People get it with `LastbookES-<version>-Setup.exe` (step 7 below).

Spain's own documents (`derived/spain-library`) travel inside the app: setup puts them into every library it installs
(`engine/setup/spain.py`). Setup reaches only the hosts in `engine/setup/download.py`: the official site, Hugging Face, `github.com`
and the two hosts GitHub redirects release downloads to. `engine/setup/roots.pem` holds the only certificates it trusts.

## Build, in order

1. **Map data by community** (reads a data folder, writes 19 zips, each under GitHub's 2 GiB limit, and `regions.json`):
   `python tools/build_es_regions.py --data <data folder> --out <release folder>`
   Each zip is a map set (`docs/SETUP-API.md`, State maps): all of Spain at low zoom plus everything inside the community's box
   (`docs/regions-es-proposed.json`), with its aerial photos, terrain, buildings, water and addresses (`derived/geo/addresses/ES.sqlite`).
2. **Spanish documents**: `python tools/es_library.py --export derived/spain-library --files <folder with the official PDFs>`
3. **Small pack entry**: `derived/release/manifest-v1.json`, `{"version": 1, "packs": [<the "small" entry of the official app's manifest>]}`.
4. **The runner of the local AI**: a folder with `lastbook-llm.exe`, its DLLs and a `SHA256SUMS.txt` that matches them. The official app's
   files are signed after its sums are made, so check their signatures and make the sums again from the files you will ship.
5. **The app** (Git Bash, CPython 3.14.10 or later from the PSF's NuGet package, `packaging/requirements_app.txt` installed):

   ```bash
   PY=/c/path/to/python.exe OUT='C:\path\to\build-app' LLM='C:\path\to\runner' \
   MAPS_FROM='C:\path\to\release folder' MAPS_BASE='https://github.com/<owner>/<repo>/releases/download/<tag>' \
   SPAIN_LIBRARY='C:\path\to\derived\spain-library' APP_NAME=LastbookES APP_TITLE='Lastbook ES' \
   bash packaging/build_app_windows.sh
   ```

   It stops if a package scan fails, if the TLS roots do not verify every host, or if a manifest address is not on the allowed hosts.
6. **Checks**: `python app/tests/test_setup.py` (96 tests) and `python packaging/check_tls_roots.py`.
7. **The installer** (the same Python, and llvm-mingw's clang for its front, as in `packaging/llm/build_llm_windows.sh`):

   ```bash
   PY=/c/path/to/python.exe DIST='C:\path\to\build-app\dist\LastbookES' OUT='C:\path\to\build-setup' \
   CLANG='C:\path\to\llvm-mingw-20261020-ucrt-x86_64\bin\clang.exe' bash packaging/build_setup_windows.sh
   ```

   It makes `OUT\LastbookES-<version>-Setup.exe`. It installs for this user only, with no administrator prompt, into
   `%LOCALAPPDATA%\Programs\LastbookES`, with its own Start menu shortcut, Apps & features entry (`HKCU\...\Uninstall\LastbookES`) and
   uninstaller (`Uninstall Lastbook ES.exe`), so the official Lastbook is never touched. The names and the version come from
   `packaging/lastbook_setup.py`. The version is Lastbook ES's own (the release tag without its "v"; 1.1.11 in v1.0.0, which still
   checked the official site): About's "Check for updates" (`engine/update.py`) reads `app-v1.json` from this fork's latest release.

## Publish

Upload `LastbookES-<version>-Setup.exe` and the 19 `Lastbook-Map-ES-*.zip` files as assets of the release `MAPS_BASE` names. Each
zip's name, size and SHA-256 is in the manifest inside the app, so a zip that changes needs a new build (of the app and the installer).
`manifest-v1.json` (from `dist\LastbookES\_internal\setup`) goes to the release too: setup reads the latest release's copy once when it
opens online, and it can add maps but never change a pinned one. A `SHA256SUMS.txt` of every asset lets people check their download.
Every release also carries `app-v1.json`, which the update check reads (display information only, nothing is downloaded):
`{"version": 1, "published": "<date>", "windows": {"version": "<version>", "min_os": "10.0"}}`. Map zips that have not changed need no
new upload: the manifest may keep pointing at an earlier release's assets, which are never deleted.

## Try it with no internet

Put the Small zip and the community zip, named as in the manifest, in `%LOCALAPPDATA%\LastbookES` of a profile with no library
(`APPDATA` and `LOCALAPPDATA` may be set to empty folders for the test). Setup lists them as files already on the computer, checks their
SHA-256 and installs them without downloading.
