# MIRACO Pro in PC mode, seen from Linux (2026-09-13)

Put the MIRACO in PC mode (its own menu; it says "Currently in PC mode, please do not disconnect" and asks for
Revo Scan 5.4.1). Over USB it then re-enumerates as `2207:0005 REVO_PRODUCT` (manufacturer Chishine3d): a plain
UVC composite with two cameras and an extension unit, structurally a clone of the RANGE (2207:110c) that range.py
already drives. MTP and ADB disappear in this mode (PIDs: 0006 = ADB only, 0017 = ADB+MTP File Transfer,
0005 = PC mode).

| node | name | formats |
|---|---|---|
| /dev/video0 | DepthCam | YUYV 800x600 / 1600x1200 (10, 5 fps); **'Y16 ' 800x600 (10, 5 fps) = depth only; 'Y16 ' 800x1200 @30 fps = depth u16 + IR left u8 + IR right u8** (960000 + 480000 + 480000 bytes); Y16 1600x1200 |
| /dev/video1 | DepthCam metadata | UVC metadata node |
| /dev/video2 | RGBCam | MJPG 2000x1500 @30 fps |
| /dev/video3 | RGBCam metadata | UVC metadata node |

Extension unit 4, GUID c2211a6d-787e-3c4a-ac06-7e434b676f9a, 14 controls (selectors 1-8, 10, 14, 16-19): the
same unit and selector mask the RANGE uses (1/2 file read, 3 set property, 7 execute a shell line, 0x0E read
property). Not yet exercised on the MIRACO. The projector is off by default, so depth is empty until it is
turned on (on the RANGE: `echo s 0x922 1 >/dev/rk_preisp` through selector 7; the MIRACO runs Android on an
RK3588, so this may differ).

Captured here with `v4l2-ctl --stream-mmap` and nothing from Revopoint: ir_left.png, ir_right.png (the shop,
projector off), depth_projector_off.png (mostly empty), rgb.jpg (2000x1500, downsized).

Why it matters: the IR cameras are what see the ring markers on the board, so marker-based tracking from Linux
becomes possible in principle, and a live scanning mode (depth + IR + RGB at 30 fps) is within reach with the
RANGE driver as the starting point. Full descriptor dump and inspection notes: the agent report of 2026-09-13
(scratchpad/miraco_pcmode_lsusb_v.txt at the time); PID map and timeline in the same report.

## Public research (2026-09-13, web agent)
- PC mode is USB only (Revopoint staff on the forum); Wi-Fi does file transfer only. Needs Revo Scan 5 on the PC
  officially; we do not need it.
- Revopoint's SDK ("3DCamera SDK", paid, MIRACO not on its list) has leaked to GitHub: rccn-dev/revopoint_camera
  (Linux .so + headers) and tycoon0804/revopoint_ros. Its stream formats: Z16 (depth), Z16Y8Y8 (depth + left/right
  IR, what our 800x1200 Y16 frame is), PAIR (two IR), MJPG/RGB8, I8DS (IR preview), XZ32 (point cloud), GRAY.
  Depth unit via PROPERTY_EXT_DEPTH_SCALE. It also has STREAM_FORMAT_IR_MARKERS and getMarkersInfo(): the scanner
  detects the ring markers itself and can hand over marker coordinates, so marker tracking may not need our own
  detector.
- The sibling scanners' LAN protocol (TamedTornado/revopoint-pop3-linux-wifi, HazenBabcock/revopoint-python):
  lighttpd CGI zx_cmd.cgi / zx_media.cgi, frames with magic 0x11223344 + length, QuickLZ-compressed, not encrypted;
  register pokes via `system_cmd=echo s 0xb01 1 > /dev/rk_preisp` (0xb00 LED master, 0xb01 IR projector,
  0x910-0x912 exposure). Over USB the same box is UVC + XU (RANGE, MIRACO), where selector 7 executes such a line.
- Depth in Revo Scan caches: MIRACO 800x600 u16, 0.1 mm/unit (ifilipis/metrox, X3msnake/revoscan-frame-player).
Next steps: XU reads first (GET_LEN on selectors 1,2,7,14; read_file /tmp/inited and /data/camparam/Pl.bin), then
try the projector line through selector 7 and stream Y16 800x1200 with depth present; then look for the marker
stream/property.

## Projector and IR, verified with the cup (2026-09-13)
The RANGE projector recipe (selector 7: `echo s 0xb00 1`, `0xb01 1`, `0x922 1 >/dev/rk_preisp`) DOES work on the
MIRACO: depth went from ~0.4% to 27-41% valid with a cup at 280 mm. The earlier "dark IR" was just IR auto-exposure
adapting to the speckle. The Y16 800x1200 frame layout is confirmed from the bytes: depth u16 800x600, then IR
left u8 800x600, then IR right u8 800x600 (row smoothness 0.8 at width 800 vs 58 at 400; bright centres of the two
planes sit near the depth silhouette with a horizontal offset between them = a stereo pair). IR frames are raw camera
views (wider, unrectified); depth is in the rectified left frame. IR-dark materials (this shiny black part) show
black in IR while the lit table around them is bright. Firmware string via /tmp/inited: v301.2.15.1204.
