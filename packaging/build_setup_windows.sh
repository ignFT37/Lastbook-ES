#!/bin/bash
# <NAME>-<version>-Setup.exe from a built app folder: per-user, no administrator prompt. Run in Git Bash on Windows:
#
#   PY=/c/path/to/python.exe DIST='C:\path\to\dist\LastbookES' OUT='C:\path\to\output' CLANG='C:\path\to\llvm-mingw\bin\clang.exe' \
#   bash packaging/build_setup_windows.sh
#
# PY    The same CPython 3.14 as build_app_windows.sh (PyInstaller and pythonnet installed). Nothing is installed or downloaded.
# DIST  The app folder that build_app_windows.sh made (Lastbook ES: dist\LastbookES, with LastbookES.exe).
# OUT   Build output. The installer lands at OUT\<NAME>-<version>-Setup.exe.
# CLANG llvm-mingw's clang.exe (the toolchain of packaging/llm/build_llm_windows.sh), for the front in packaging/setup_stub/.
# SIGNTOOL, SIGNING_DLIB, SIGNING_METADATA optionally enable Azure Artifact Signing.
# Set all three to Windows paths. The metadata file names the signing account and certificate profile.
#
# Lastbook ES: the names (NAME, TITLE, APP_EXE, UNINSTALLER, ICON) and VERSION are read from lastbook_setup.py, so this script, the
# installer and its front always agree: LastbookES-<version>-Setup.exe installs LastbookES.exe in %LOCALAPPDATA%\Programs\LastbookES.
#
# Steps: the icon, the uninstaller (lastbook_setup.py without a payload), app.zip (the app folder, the uninstaller and the icon),
# then the installer (the same program with app.zip inside). The network and other unused modules are left out of both programs.
set -euo pipefail
HERE="$(dirname "$(realpath "${BASH_SOURCE[0]}")")"
HEREW="$(cygpath -w "$HERE")"
PY="${PY:?Set PY to a CPython 3.14 python.exe}"
DIST="${DIST:?Set DIST to the built app folder}"
OUT="${OUT:?Set OUT to an output folder}"
const() {   # a plain string constant of lastbook_setup.py
  "$PY" -I -B -c "import ast,sys;t=open(sys.argv[1],encoding='utf-8').read();print([n.value.value for n in ast.parse(t).body if isinstance(n,ast.Assign) and getattr(n.targets[0],'id','')==sys.argv[2]][0])" "$HEREW\\lastbook_setup.py" "$1"
}
VERSION="$(const VERSION)"; NAME="$(const NAME)"; TITLE="$(const TITLE)"; APP_EXE="$(const APP_EXE)"; UNINSTALLER="$(const UNINSTALLER)"; ICON="$(const ICON)"
# They go into file names, a C header, a resource script and PyInstaller's version file: letters, digits and spaces only.
for v in "$NAME" "$TITLE" "${APP_EXE%.exe}" "${UNINSTALLER%.exe}" "${ICON%.ico}"; do
  [[ "$v" =~ ^[A-Za-z0-9\ ]+$ ]] || { echo "lastbook_setup.py: unexpected name '$v'" >&2; exit 1; }
done
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || { echo "lastbook_setup.py: unexpected VERSION '$VERSION'" >&2; exit 1; }
SETUP="$NAME-$VERSION-Setup"
OUTU="$(cygpath -u "$OUT")"
mkdir -p "$OUTU/tmp/localappdata" "$OUTU/tmp/pyinstaller" "$OUTU/payload"
export TEMP="$OUT\\tmp" TMP="$OUT\\tmp" LOCALAPPDATA="$OUT\\tmp\\localappdata" PYINSTALLER_CONFIG_DIR="$OUT\\tmp\\pyinstaller"
export PYTHONDONTWRITEBYTECODE=1 PYTHONNOUSERSITE=1 PYTHONHASHSEED=0
unset PYTHONHOME PYTHONPATH
test -f "$(cygpath -u "$DIST")/$APP_EXE" || { echo "no $APP_EXE in $DIST" >&2; exit 1; }

sign_target() {
  if [ -z "${SIGNTOOL:-}${SIGNING_DLIB:-}${SIGNING_METADATA:-}" ]; then
    return
  fi
  : "${SIGNTOOL:?Set all three signing paths}"
  : "${SIGNING_DLIB:?Set all three signing paths}"
  : "${SIGNING_METADATA:?Set all three signing paths}"
  powershell.exe -NoProfile -NonInteractive -File "$HEREW\\sign_windows.ps1" \
    -Path "$1" -SignTool "$SIGNTOOL" -Dlib "$SIGNING_DLIB" -Metadata "$SIGNING_METADATA"
}
sign_target "$DIST"

cp "$HERE/icon/Lastbook.ico" "$OUTU/$ICON"

cat > "$OUTU/version.txt" <<EOF
VSVersionInfo(ffi=FixedFileInfo(filevers=(${VERSION//./, }, 0), prodvers=(${VERSION//./, }, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [StringStruct('CompanyName', '$TITLE'), StringStruct('FileDescription', '$TITLE Setup'),
    StringStruct('FileVersion', '$VERSION'), StringStruct('ProductName', '$TITLE'), StringStruct('ProductVersion', '$VERSION')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])])
EOF

build() {   # name, output folder, extra PyInstaller arguments
  local name="$1" dir="$2"; shift 2
  "$PY" -I -B -m PyInstaller --noconfirm --clean --onefile --windowed --noupx --name "$name" --icon "$OUT\\$ICON" \
    --version-file "$OUT\\version.txt" --distpath "$OUT\\$dir" --workpath "$OUT\\work-$dir" --specpath "$OUT\\work-$dir" \
    --hidden-import clr --collect-all clr_loader --collect-all pythonnet \
    --exclude-module tkinter --exclude-module unittest --exclude-module pydoc --exclude-module doctest --exclude-module pdb \
    --exclude-module setuptools --exclude-module pkg_resources --exclude-module _distutils_hack --exclude-module numpy \
    --exclude-module ssl --exclude-module _ssl --exclude-module socket --exclude-module _socket --exclude-module asyncio --exclude-module multiprocessing \
    --exclude-module http --exclude-module xml --exclude-module _decimal \
    "$@" "$HEREW\\lastbook_setup.py"
}
build "${UNINSTALLER%.exe}" uninstall
sign_target "$OUT\\uninstall\\$UNINSTALLER"
"$PY" -I -B - "$DIST" "$OUT\\uninstall\\$UNINSTALLER" "$OUT\\$ICON" "$OUT\\payload\\app.zip" <<'ZIP'
import os, sys, zipfile
dist, uninstaller, icon, out = sys.argv[1:]
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, dirs, files in os.walk(dist):
        dirs.sort()
        for f in sorted(files):
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, dist).replace(os.sep, '/'))
    z.write(uninstaller, os.path.basename(uninstaller))
    z.write(icon, os.path.basename(icon))
ZIP
build "$SETUP" setup --add-data "$OUT\\payload\\app.zip;."
sign_target "$OUT\\setup\\$SETUP.exe"

# The front (packaging/setup_stub/lastbook_setup_stub.c): what the person runs. It allows DLLs from System32 only, then runs the
# installer above from a private folder of its own, so nothing planted beside a downloaded Setup.exe is loaded (audit N-DLL).
CLANG="${CLANG:?Set CLANG to the clang.exe of llvm-mingw}"
CLANGU="$(cygpath -u "$CLANG")"; BIN="$(dirname "$CLANGU")"
STUB="$OUTU/stub"; rm -rf "$STUB"; mkdir -p "$STUB"
cp "$OUTU/$ICON" "$STUB/$ICON"
printf '#define SETUP_TITLE L"%s Setup"\n#define SETUP_FOLDER L"%sSetup-"\n#define SETUP_INNER L"%s Setup.exe"\n' "$TITLE" "$NAME" "$TITLE" > "$STUB/names.h"
cat > "$STUB/stub.rc" <<RC
1 ICON "$ICON"
1 VERSIONINFO
FILEVERSION ${VERSION//./,},0
PRODUCTVERSION ${VERSION//./,},0
BEGIN
  BLOCK "StringFileInfo"
  BEGIN
    BLOCK "040904B0"
    BEGIN
      VALUE "CompanyName", "$TITLE"
      VALUE "FileDescription", "$TITLE Setup"
      VALUE "FileVersion", "$VERSION"
      VALUE "ProductName", "$TITLE"
      VALUE "ProductVersion", "$VERSION"
      VALUE "Comments", "build ${STAMP:-$(date -u +%Y%m%d-%H%M%S)}"
    END
  END
  BLOCK "VarFileInfo"
  BEGIN
    VALUE "Translation", 0x409, 1200
  END
END
RC
( cd "$STUB" && "$BIN/llvm-windres" stub.rc -O coff -o stub.res )
"$CLANGU" -O2 -s -ffreestanding -fno-stack-protector -nostdlib -mwindows -municode -Wl,-e,WinMainCRTStartup \
  -include "$(cygpath -m "$STUB/names.h")" \
  "$(cygpath -m "$HERE/setup_stub/lastbook_setup_stub.c")" "$(cygpath -m "$STUB/stub.res")" -lkernel32 -luser32 -o "$(cygpath -m "$STUB/stub.exe")"
imports="$("$BIN/llvm-objdump" -p "$(cygpath -m "$STUB/stub.exe")" | sed -n 's/^ *DLL Name: //p' | tr 'A-Z' 'a-z' | sort -u | tr '\n' ' ')"
[ "$imports" = "kernel32.dll user32.dll " ] || { echo "the setup front imports more than kernel32 and user32: $imports" >&2; exit 1; }
"$PY" -I -B - "$(cygpath -m "$STUB/stub.exe")" "$(cygpath -m "$OUTU/setup/$SETUP.exe")" "$(cygpath -m "$OUTU/$SETUP.exe")" <<'JOIN'
import struct, sys
stub, inner, out = sys.argv[1:]
data = open(inner, 'rb').read()
with open(out, 'wb') as f:
    f.write(open(stub, 'rb').read()); f.write(data); f.write(struct.pack('<Q', len(data)) + b'LBSETUP1')
JOIN
sign_target "$OUT\\$SETUP.exe"
ls -l "$OUTU/$SETUP.exe"
sha256sum "$OUTU/$SETUP.exe"
