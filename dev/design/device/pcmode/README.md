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
