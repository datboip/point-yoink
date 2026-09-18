#!/usr/bin/env bash
# Build a .deb for PointYoink. Bundles the pip-only Python libs (customtkinter,
# trimesh + pure-python deps) and declares the rest as apt dependencies.
# The package, command, install dir and desktop entry are all "point-yoink"
# (so it never reads as "pointy oink"); the module file and ~/.config dir stay
# "pointyoink" internally so existing settings/records are not orphaned.
set -e
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
# app version as written in pointyoink.py; a pre-release suffix becomes Debian's "~" so 1.0.0~rc1 sorts BEFORE 1.0.0
VER="${1:-$(grep -oP 'VERSION = "\K[0-9A-Za-z.-]+' "$ROOT/pointyoink.py")}"
VER="${VER//-/~}"                     # an explicit version argument gets the same Debian pre-release form
BUILD="$ROOT/packaging/build"
NAME="point-yoink"
LIB="usr/lib/point-yoink"
PKG="$BUILD/${NAME}_${VER}_amd64"

rm -rf "$BUILD"
mkdir -p "$PKG/DEBIAN" \
         "$PKG/$LIB/vendor" \
         "$PKG/usr/bin" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/512x512/apps" \
         "$PKG/usr/share/doc/point-yoink"

# --- vendor the pip-only deps (fast-simplification is a compiled ext -> arch-specific deb) ---
# pinned to the versions listed in THIRD_PARTY_NOTICES.md; bump both together
"$ROOT/venv/bin/pip" install --quiet --target "$PKG/$LIB/vendor" customtkinter==6.0.0 darkdetect==0.8.0 trimesh==5.1.0 pyglet==1.5.31 fast-simplification==0.2.0 pyopengltk==0.0.4 PyOpenGL==3.1.10 packaging==26.3
# drop things provided by apt (PIL/ImageTk = system tk build; numpy/matplotlib/networkx are apt)
V="$PKG/$LIB/vendor"
rm -rf "$V"/PIL* "$V"/Pillow* "$V"/pillow* "$V"/numpy* "$V"/matplotlib* "$V"/networkx* "$V"/bin "$V"/__pycache__ 2>/dev/null || true
find "$V" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --- app files (the module files keep their names, they just live under point-yoink/) ---
for f in pointyoink.py viewer.py process.py cutplane.py fuse.py wifi.py shade.py meshview.py glview.py align.py register.py icon.png; do
  cp "$ROOT/$f" "$PKG/$LIB/"
done
# whole assets tree: device help screenshots AND the icon set (assets/icons/png/<state>/<size>/*.png + manifest)
mkdir -p "$PKG/$LIB/assets" && cp -r "$ROOT"/assets/. "$PKG/$LIB/assets/"
find "$PKG/$LIB/assets" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
cp "$ROOT/icon.png"      "$PKG/usr/share/icons/hicolor/512x512/apps/point-yoink.png"
cp "$ROOT/LICENSE" "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$ROOT/THIRD_PARTY_NOTICES.md" "$PKG/usr/share/doc/point-yoink/" 2>/dev/null || true

# --- launcher (the command is `point-yoink`) ---
cat > "$PKG/usr/bin/point-yoink" <<EOF
#!/bin/sh
export PYTHONPATH="/$LIB/vendor:\$PYTHONPATH"
exec python3 /$LIB/pointyoink.py "\$@"
EOF
chmod 755 "$PKG/usr/bin/point-yoink"

# --- desktop entry ---
cat > "$PKG/usr/share/applications/point-yoink.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PointYoink
GenericName=3D Scan Grabber
Comment=Pull 3D scans off a Revopoint MIRACO over USB
Exec=point-yoink
Icon=point-yoink
Terminal=false
Categories=Graphics;Utility;
Keywords=3d;scan;scanner;revopoint;miraco;mtp;ply;
StartupWMClass=Tk
EOF

# --- control ---
cat > "$PKG/DEBIAN/control" <<EOF
Package: point-yoink
Version: ${VER}
Architecture: amd64
Maintainer: datboip <datboip@users.noreply.github.com>
Depends: python3, python3-tk, python3-pil, python3-pil.imagetk, python3-numpy, python3-matplotlib, python3-networkx, jmtpfs, rsync, xdg-utils, fuse3 | fuse
Recommends: ffmpeg
Conflicts: pointyoink
Replaces: pointyoink
Section: graphics
Priority: optional
Homepage: https://github.com/datboip/point-yoink
Description: Pull 3D scans off a Revopoint MIRACO over USB
 PointYoink copies finished scans off a Revopoint MIRACO / MIRACO Pro
 scanner on Linux over USB, with no Revo Scan, Windows, or cloud needed.
 It shows your projects with previews and exports standard PLY, STL, OBJ, GLB.
EOF

chmod -R u+rwX,go+rX "$PKG"
dpkg-deb --root-owner-group --build "$PKG" >/dev/null
echo "built: $(ls "$BUILD"/*.deb)"
