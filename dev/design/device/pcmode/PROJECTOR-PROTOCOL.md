# MIRACO projector control — findings from the leaked SDK (2026-09-13)

## Why the RANGE recipe partially worked
We measured the RANGE's projector command (selector 7: `echo s 0xb00 1`, `0xb01 1`,
`0x922 1 >/dev/rk_preisp`) on the real MIRACO and got a real result: depth went from
under 1% valid to 27-41% valid with a cup in frame, verified against actual images.
So it does something.

The leaked SDK (disassembled `lib3DCamera.so` v3.2.229) says it should not: it gates
LED/laser control by camera type (from the serial number), and MIRACO (type 0x14) is
in neither the LED-control set `{POP_2, MINI, RANGE}` nor implied to use the same path.
MIRACO instead has its own case in `laserEnableCtrl`: it reads a per-unit resonant
frequency from `/data/g_resonant_frequency`, then drives the projector through a
different, longer command:
  `echo s 0x4a0 8 0x5a 0xf1 0x06 <freq_hi> <freq_lo> 0x00 0x00 0xA5 >/dev/rk_preisp`
  (0x16 in place of 0x06 for the "far" side), plus `echo s 0xb04 0|1`.
MIRACO is also the only type with far/near camera pairs (`0x23` selects which), which
may explain why our two IR planes look like different lenses rather than a stereo pair
of the same lens (see the main pcmode README).

Best guess: the RANGE recipe's `0xb00`/`0xb01` toggle something MIRACO still shares
(maybe an LED, or a fallback), enough for a little usable depth, but not the actual
resonant-MEMS laser drive, which is why coverage is well below what the scanner's own
One-tap models show.

## What the proper sequence would be (untested — do not run without a plan)
1. Read `/data/g_resonant_frequency` through the XU file-read selector (safe, read-only;
   same mechanism as reading Pl.bin and /tmp/inited, both already verified working).
2. Set far/near camera state: extension property 0x23 (`PROPERTY_EXT_FAR_NEAR_CAMERA_STATE`).
3. Send the 0x4a0 long command with that frequency for the active side.
4. `echo s 0xb04 1` to enable.

This pokes a laser/projector driver with values reverse-engineered from a disassembly,
not confirmed on this exact firmware. Read step 1 is safe and worth doing any time.
Steps 2-4 should only be tried deliberately, ideally with the scanner's own screen
watched for anything unexpected, not run automatically in a build.

## Full property/register map (leaked SDK, for anything else we build)
PROPERTY_TYPE (per-stream): GAIN=0, EXPOSURE=1, FRAMETIME=2, FOCUS=3, AUTO_FOCUS=4,
AUTO_EXPOSURE=5, AUTO_WHITEBALANCE=6, WHITEBALANCE=7, WB_R=8, WB_B=9, WB_G=0x10.

PROPERTY_TYPE_EXTENSION (selected): DEPTH_SCALE=0, TRIGGER_MODE=1, HDR_BRIGHTNESS=3,
FAST_SCAN=0xA, PAUSE_DEPTH=0xD, RESUME_DEPTH=0xE, EXPOSURE_TIME_RGB=0x15,
FAR_NEAR_CAMERA_STATE=0x23, AUTO_EXPOSURE_MODE=0x912, DEPTH_ROI=0x913, HDR_MODE=0x914,
DEPTH_RANGE=0x707, FILTER_SWITCH=0x70a, LED_ON_OFF=0xb00, LED_CTRL=0xb01,
LED_LASER_CTRL_SUPPORT=0xb07, LASER_CAL_PC_MARKERS_FLAG=0xb0f, MARKER_SUPPORT_RADIUS=0xb19.

Register->function map (rk_preisp side): 0x910=frametime, 0x911=exposure(depth),
0x903=gain(depth), 0x912=auto-exposure mode, 0x913=depth ROI, 0x702=stream resolution,
0x707=depth range, 0x70a=filter switch, 0xa04=multiframe fusion, 0xb00=LED on/off,
0xb01=LED+laser ctrl (RANGE family), 0xb04=laser enable (MIRACO path via 0x4a0),
0xb07=IR luminance, 0x922=laser on/off (RANGE family).

USB transport confirmed: UVC extension unit 4, interface 0, libusb control transfer
bmRequestType 0x21 set / 0xa1 get, bRequest 0x09 SET_CUR / 0x81 GET_CUR,
wValue = selector<<8, wIndex = unit<<8 | interface. Matches range.py's ioctl exactly.

Markers: computed ON the scanner from the IR pair (cs::markersPoints, using the Q
reprojection matrix), returned as {point, normal, confidence, radius, epipolarError}
structs via STREAM_FORMAT_IR_MARKERS / getMarkersInfo() — not raw images. So true
marker-based tracking from our side would mean requesting that stream format, not
running our own detector on the IR frames.

Full symbol/string dump and SDK source tree are in scratchpad/rp/ from this session
(sdk/, bin/new_x64.so, bin/syms.txt) — not committed to the repo (large/binary).

## Physical sensor bar (photo from the user, 2026-09-13)
camera-bar.jpg: the front sensor strip. Top to bottom: a lens with a lit green indicator, a dark
lens, a black square window (likely the structured-light emitter, not a lens), then more lenses
below with a second lit green indicator at the bottom. The user counted about 6 lenses and roughly
10 small lights total. Matches the SDK finding that MIRACO uniquely has near AND far camera pairs
(FAR_NEAR_CAMERA_STATE), so there are two stereo IR pairs plus the RGB camera plus the emitter,
not just one pair like the RANGE.

User's observation: during a live PC-mode session using the RANGE's projector command (0xb00/0xb01/
0x922), no indicator lit; the instant USB was unplugged, one lit. Read: not evidence the command
did nothing (we measured real depth, 27-41% valid, in that same session) - more likely evidence it
is triggering the wrong path (a fallback that gives partial depth without the true laser firing hard
enough to light its own indicator), consistent with the SDK's per-type gating. The light-on-unplug
is most likely just the documented reboot-on-stream-stop behaviour (see range.py notes), not caused
by the projector command succeeding or failing.

## `laserEnableCtrl` 0x4a0 command - traced but NOT sent (2026-09-13)
Full disassembly trace of `cs::Camera::laserEnableCtrl(LED_CTRL_TYPE)` (0x248cce) and
`cs::Camera::readLaserFrequ(FAR_NEAR_STATE&)` (0x237c3c), done because the app's existing
projector command (0xb00/0xb01/0x922) already gives usable depth, and this was purely an
attempt to verify a more targeted MIRACO-specific command before ever considering sending it.

Confirmed exactly, byte-for-byte, via string-table + argument-register tracing:
- Calibration bytes come from `/data/g_resonant_frequency` on the device (2 raw bytes, read
  earlier this session as `0x0D51`), copied verbatim into the command.
- Two full command templates exist in rodata, each preceded by its own debug-log string:
  `"near camera larser freque, %02x %02x"` -> `echo s 0x4a0 8 0x5a 0xf1 0x06 0x%02x 0x%02x 0x00 0x00 0xA5 > /dev/rk_preisp`
  `"far camera larser freque, %02x %02x"`  -> same with `0x16` instead of `0x06`.

NOT resolved, and this is why nothing was sent:
- `FAR_NEAR_STATE` (0/1/2) is a raw pass-through of the `XU_NEAR_FAR_STATE_CHANGE` extension-unit
  register (see `cs::UvcCamera::getFarNearCameraState`) with no symbolic near=X/far=Y label
  anywhere in the binary - it's whatever the firmware source calls it, which didn't leak.
- The actual near/far *template* choice inside `laserEnableCtrl` is gated on comparing two
  characters (position 13, length 2) of the connected unit's own serial number against the
  literal string `"2M"` - a hardware-variant check, not a live distance-mode switch - plus a
  second, separately-flagged code path ([this+0x380], set elsewhere, not yet traced) that can
  reach the same template through a different stored byte pair.
- Confirmed empirically instead (2026-09-13, live device, read-only, no writes): the device's
  own scan screen has a literal Near/Far toggle button (see the two screenshots pulled this
  session) - proves the mode is real and user-facing, but doesn't by itself resolve which raw
  register value maps to which label without a supervised live read-back while toggling it.
- Conclusion: not enough to respons­ibly send. Since the existing 0xb00/0xb01/0x922 projector
  command already works, this is parked rather than guessed at.

## Real depth-to-color calibration files found (2026-09-13)
Tracing `cs::Camera::getExtrinsics(Extrinsics&)` (0x235b1e) and `getDistort(STREAM_TYPE, Distort&)`
(0x235dc0) turned up the on-device calibration files needed for genuine pixel-accurate depth-to-color
alignment (as opposed to the resize-and-blend "Combined" view we shipped before), all readable with
the exact same safe file-read mechanism already used for the depth intrinsics (`Pl.bin`):

- `/data/camparam/Prgb.bin` - color camera intrinsics, identical binary layout to `Pl.bin`
  (u16 calib width, u16 calib height, then 9x float32 row-major K matrix).
- `/data/camparam/LC_RT.bin` - 48 bytes: 9x float32 row-major rotation + 3x float32 translation
  (mm), confirmed via `memcpy(..., 0x30)`. "LC" = left(depth)-to-color. Transform convention
  assumed as `P_color = R @ P_depth + T` (matches a synthetic identity-calibration round-trip
  test in range.py, not yet confirmed against the device's real file contents).
- `/data/camparam/Distort.bin` - 20 bytes = 5x float32 (k1, k2, p1, p2, k3), confirmed via
  `memcpy(..., 0x14)` and the "get distort of rgb failed" log string right next to the path.
- Also seen but not yet used: `/data/camparam/camparamLR/P.bin`, `Q.bin` (stereo rectification),
  `mapparamL.bin`, `mapparamR.bin` (rectification maps), `metroExtra.bin`.

Implemented in `range.py` (`XU.rgb_intrinsics`, `XU.extrinsics`, `XU.rgb_distort`,
`reproject_color_to_depth`) and wired into the Live view's Combined mode in `pointyoink.py`
(0.9.33-pre). Falls back to the old blend if any of the three reads fail. Not yet verified
against a live read of the real files - only unit-tested with synthetic identity calibration.
