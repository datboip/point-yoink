#!/usr/bin/env bash
# Build a .deb for PointYoink. Bundles the pip-only Python libs (customtkinter,
# trimesh + pure-python deps) and declares the rest as apt dependencies.
set -e
VER="${1:-$(grep -oP 'VERSION = "\K[0-9.]+' "$(dirname "$0")/../pointyoink.py")}"
ROOT="$HOME/pointyoink"
BUILD="$ROOT/packaging/build"
PKG="$BUILD/pointyoink_${VER}_amd64"

rm -rf "$BUILD"
mkdir -p "$PKG/DEBIAN" \
         "$PKG/usr/lib/pointyoink/vendor" \
         "$PKG/usr/bin" \
         "$PKG/usr/share/applications" \
         "$PKG/usr/share/icons/hicolor/512x512/apps" \
         "$PKG/usr/share/doc/pointyoink"

# --- vendor the pip-only deps (fast-simplification is a compiled ext -> arch-specific deb) ---
"$ROOT/venv/bin/pip" install --quiet --target "$PKG/usr/lib/pointyoink/vendor" customtkinter trimesh "pyglet<2" fast-simplification
# drop things provided by apt (PIL/ImageTk = system tk build; numpy/matplotlib/networkx are apt)
V="$PKG/usr/lib/pointyoink/vendor"
rm -rf "$V"/PIL* "$V"/Pillow* "$V"/pillow* "$V"/numpy* "$V"/matplotlib* "$V"/networkx* "$V"/bin "$V"/__pycache__ 2>/dev/null || true
find "$V" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# --- app files ---
cp "$ROOT/pointyoink.py" "$PKG/usr/lib/pointyoink/"
cp "$ROOT/viewer.py"     "$PKG/usr/lib/pointyoink/"
cp "$ROOT/process.py"    "$PKG/usr/lib/pointyoink/"
cp "$ROOT/cutplane.py"   "$PKG/usr/lib/pointyoink/"
cp "$ROOT/fuse.py"       "$PKG/usr/lib/pointyoink/"
cp "$ROOT/range.py"      "$PKG/usr/lib/pointyoink/"
cp "$ROOT/wifi.py"       "$PKG/usr/lib/pointyoink/"
cp "$ROOT/icon.png"      "$PKG/usr/lib/pointyoink/"
cp "$ROOT/icon.png"      "$PKG/usr/share/icons/hicolor/512x512/apps/pointyoink.png"
cp "$ROOT/LICENSE" "$ROOT/README.md" "$ROOT/CHANGELOG.md" "$PKG/usr/share/doc/pointyoink/" 2>/dev/null || true

# --- launcher ---
cat > "$PKG/usr/bin/pointyoink" <<'EOF'
#!/bin/sh
export PYTHONPATH="/usr/lib/pointyoink/vendor:$PYTHONPATH"
exec python3 /usr/lib/pointyoink/pointyoink.py "$@"
EOF
chmod 755 "$PKG/usr/bin/pointyoink"

# --- desktop entry ---
cat > "$PKG/usr/share/applications/pointyoink.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=PointYoink
GenericName=3D Scan Grabber
Comment=Pull 3D scans off a Revopoint MIRACO over USB
Exec=pointyoink
Icon=pointyoink
Terminal=false
Categories=Graphics;Utility;
Keywords=3d;scan;scanner;revopoint;miraco;mtp;ply;
StartupWMClass=Tk
EOF

# --- control ---
cat > "$PKG/DEBIAN/control" <<EOF
Package: pointyoink
Version: ${VER}
Architecture: amd64
Maintainer: datboip <datboip@users.noreply.github.com>
Depends: python3, python3-tk, python3-pil, python3-pil.imagetk, python3-numpy, python3-matplotlib, python3-networkx, jmtpfs, rsync, xdg-utils, fuse3 | fuse
Recommends: v4l-utils
Section: graphics
Priority: optional
Homepage: https://github.com/datboip/pointyoink
Description: Pull 3D scans off a Revopoint MIRACO over USB
 PointYoink copies finished scans off a Revopoint MIRACO / MIRACO Pro
 scanner on Linux over USB, with no Revo Scan, Windows, or cloud needed.
 It shows your projects with previews and exports standard PLY, STL, OBJ, GLB.
EOF

chmod -R u+rwX,go+rX "$PKG"
dpkg-deb --root-owner-group --build "$PKG" >/dev/null
echo "built: $(ls "$BUILD"/*.deb)"
