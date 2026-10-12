#!/usr/bin/env bash
# M3: empaqueta club-autotalplay como AppImage (Linux x86_64).
#
# No usa appimagetool (su propio AppImage no monta FUSE en este entorno);
# en su lugar arma el AppImage Type2 a mano:
#   [runtime ELF] + [squashfs del AppDir]
# con `appimage-runtime-x86_64` (stub Type2) + `mksquashfs`.
#
# Salida: dist/Club-AutotalPlay-x86_64.AppImage
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$REPO/.venv"
APP="club-autotalplay"
NAME="Club-AutotalPlay"
RUNTIME="$HOME/.local/bin/appimage-runtime-x86_64"
BUILD="$REPO/build"
APPDIR="$BUILD/AppDir"
DIST="$REPO/dist"
OUT="$DIST/${NAME}-x86_64.AppImage"

command -v mksquashfs >/dev/null || { echo "falta mksquashfs"; exit 1; }
[ -x "$RUNTIME" ] || { echo "falta runtime: $RUNTIME"; exit 1; }

VER="$("$VENV/bin/python" -c "import importlib.metadata as m; print(m.version('club-autotalplay'))")"
echo "== ${NAME} ${VER} =="

# 1. limpiar
rm -rf "$BUILD"
mkdir -p "$APPDIR/usr" "$DIST"

# 2. PyInstaller onedir (ejecutable + _internal/ con libs y assets)
echo "[1/4] pyinstaller (onedir)..."
"$VENV/bin/pyinstaller" --noconfirm --clean --onedir --name "$APP" \
  --distpath "$BUILD/dist" --workpath "$BUILD/pyi-work" --specpath "$BUILD" \
  --paths "$REPO/src" \
  --add-data "$REPO/src/club_autotalplay/assets:club_autotalplay/assets" \
  --hidden-import pystray._xorg \
  --hidden-import Xlib.display \
  --collect-all pystray \
  --collect-all Xlib \
  "$REPO/packaging/app_entry.py" >/dev/null

# AppDir layout: usr/club-autotalplay/{club-autotalplay,_internal}
mv "$BUILD/dist/$APP" "$APPDIR/usr/$APP"

# 3. AppRun + .desktop + icono
echo "[2/4] AppDir (AppRun/.desktop/icono)..."
cat > "$APPDIR/AppRun" <<EOF
#!/bin/sh
HERE="\$(dirname "\$(readlink -f "\$0")")"
exec "\$HERE/usr/$APP/$APP" "\$@"
EOF
chmod +x "$APPDIR/AppRun"

cat > "$APPDIR/${NAME}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=${NAME}
GenericName=Auto-reconnect WiFi
Comment=Mantiene la sesion de Club_Totalplay_WiFi activa
Exec=${APP} app
Icon=${NAME}
Terminal=false
Categories=Utility;Network;
StartupNotify=false
EOF

cp "$REPO/src/club_autotalplay/assets/icon.png" "$APPDIR/${NAME}.png"
cp "$REPO/src/club_autotalplay/assets/icon.png" "$APPDIR/.DirIcon"

# 4. squashfs + prepend runtime -> AppImage Type2
echo "[3/4] mksquashfs (xz)..."
# xz: el runtime Type2 solo soporta xz/zlib (no zstd).
mksquashfs "$APPDIR" "$BUILD/rootfs.squashfs" -comp xz -all-root \
  -noappend -no-progress

echo "[4/4] AppImage (runtime + squashfs)..."
# El runtime Type2 calcula el offset del squashfs desde su propio ELF;
# si su tamaño ya esta alineado, basta concatenar.
cat "$RUNTIME" "$BUILD/rootfs.squashfs" > "$OUT"
chmod +x "$OUT"

SZ=$(du -h "$OUT" | cut -f1)
echo "== listo: $OUT ($SZ) =="
echo "prueba: $OUT status"
