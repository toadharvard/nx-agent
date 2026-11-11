# Audio and input (stream S7)

How FalloutNV.exe 1.4.0.525 (as reconstructed by fnv_decomp `87eb9373`) uses DirectSound 8, DirectInput 8, XInput
and Ogg Vorbis, and how fnv-nx provides them: `shim/include/{dsound,dinput,xinput}.h` (declarations),
`platform/audio/` and `platform/input/` (implementations), `tests/s7/` (host tests).

## 1. Inventory

### Imports (from `platform/win32/imports.tsv`, S4)

| IAT slot | DLL | API | decomp thunk |
|---|---|---|---|
| `0x00fdf02c` | dinput8 | `DirectInput8Create` | `FUN_00ee7f10` |
| `0x00fdf034` | dsound | `DirectSoundCreate8` (ordinal 11) | `FUN_00ee81e0` |
| `0x00fdf394` | xinput1_3 | `XInputGetState` | direct `dllimport` (interfacemanager) |
| `0x00fdf438` | vorbisfile | `ov_open_callbacks` (**missing from imports.tsv**; the decomp shows it as the noreturn thunk `FUN_00ee81fe` -> `FUN_00fdf438`) | `FUN_00ee81fe` |
| `0x00fdf43c` | vorbisfile | `ov_clear` | `FUN_00ee81e6` |
| `0x00fdf440` | vorbisfile | `ov_pcm_total` | `FUN_00ee81ec` |
| `0x00fdf444` | vorbisfile | `ov_seekable` | `FUN_00ee81f2` |
| `0x00fdf448` | vorbisfile | `ov_read` | `FUN_00ee8204` |
| `0x00fdf44c` | vorbisfile | `ov_info` | `FUN_00ee81f8` |

* No `XInputSetState` / `XInputGetCapabilities` import: the PC game has **no rumble** and does not query
  capabilities. Both are implemented anyway (GetProcAddress users, future use).
* **Ogg Vorbis is imported, not static**: `vorbisfile.dll` (libvorbis is not in the exe). The engine embeds the
  `OggVorbis_File` in its own stream object (`OggStream`, `blk_00ae0000_od_ob1_variant.cpp`): 0x2D0 bytes, followed
  by `pFile` (+0x2D0), `iTotal`, `iInfo`, `bOpen` (+0x2DC). It reads `ov_info(this,-1)` (vorbis_info: channels,
  rate) and `ov_pcm_total(this,0)`; callbacks are `{read, seek, close=NULL, tell}`; `ov_read(.., bigendian 0,
  word 2, signed 1, ..)`. `.mp3`/`.wav` music names are rewritten to `.ogg` first.
* Bink: `BinkOpenDirectSound` is installed with `BinkSetSoundSystem` (S6 owns binkw32): Bink audio must reach
  our `IDirectSound8` (open item for S6/S7).

### COM call sites

The engine never names the SDK interfaces. It calls through stand-in classes declared per unit, often with
placeholder methods (`Com { q, r1, r2, create, caps, r5, coop }` in `bsgamesound.cpp`;
`FNVRenderInterface_00aea8b0 { Function00.., ApplyParameters, ApplyColor, .., Commit }` = the listener;
`FUN_00aea9d0_Listener::vf3C` = `SetRolloffFactor`; `S_00aeaa10_Obj::FUN_x40` = slot 16;
`IDirectInputDevice8A { Q, A, R, .. }`, `PollingDevice`, `IDIDevice8`). As for D3D9 (S6), **the slot index is the
contract**. A name-based census (`census_ai.py`, kept in the stream's private dir: parses virtual-method structs
in the 2,988 units of `build/units.json`, classifies them by SDK slot names, counts `->m(` calls) finds:

| interface | stand-in structs | slots called (index: name, calls) |
|---|---|---|
| IDirectSound8 | `SndDevice`, `Com`, `AudioInterface` | 3 CreateSoundBuffer, 4 GetCaps, 6 SetCooperativeLevel |
| IDirectSoundBuffer8 | `SndBuffer`, `BufferAF`, 5 more | 3 GetCaps 1, 4 GetCurrentPosition 2, 5 GetFormat 4, 6 GetVolume 1, 8 GetFrequency 1, 9 GetStatus 1, 11 Lock 5, 12 Play 8, 13 SetCurrentPosition 8, 15 SetVolume 6, 17 SetFrequency 1, 18 Stop 6, 19 Unlock 5, 21 SetFX 2, 23 GetObjectInPath 2 (53 calls) |
| IDirectSound3DBuffer | `Snd3DBuffer` (x3 variants) | 12 SetAllParameters 1, 16 SetMaxDistance 2, 17 SetMinDistance 3 (+ placeholder-named calls) |
| IDirectSound3DListener | placeholder classes only | 13 SetOrientation, 14 SetPosition, 15 SetRolloffFactor, 16 SetVelocity, 17 CommitDeferredSettings |
| FX params | `SndParamObject` | 3 SetAllParameters (`g_IID_SndFXParams`, via GetObjectInPath) |
| IDirectInput8A | 3 re-declarations | 3 CreateDevice, 4 EnumDevices (joysticks) |
| IDirectInputDevice8A | 6 re-declarations | 3 GetCapabilities 2, 4 EnumObjects 1, 6 SetProperty 2, 7 Acquire 3, 8 Unacquire 3, 9 GetDeviceState 3, 10 GetDeviceData 1, 11 SetDataFormat 4, 13 SetCooperativeLevel 7, 25 Poll 3 (29 calls) |

31 stand-in structs classified, 88 name-matched calls in 14 source files; placeholder-named calls (vfNN, FunctionNN)
are not counted by name, which is why the device and listener rows are listed by slot only. The counts are a lower
bound; the implementation provides **every SDK slot** of every interface, so the exact census is not load-bearing.

Engine facts the implementation honours:

* Device init (`BSWin32Audio::SetWindow`, 0x00aea230): `DirectSoundCreate8(NULL)`, `SetCooperativeLevel(hwnd,
  DSSCL_PRIORITY)`, `GetCaps` with a 96-byte DSCAPS, primary buffer `{dwSize 36, dwFlags 0x91 = PRIMARYBUFFER |
  CTRL3D | CTRLVOLUME}`, `QueryInterface(IID_IDirectSound3DListener)` on it.
* Secondary buffers (0x00aec530): `CreateSoundBuffer`, then QI `IID_IDirectSoundBuffer8`, then (3D sounds) QI
  `IID_IDirectSound3DBuffer`, `SetMinDistance`. Error `0x887800B4` (`DSERR_BUFFERTOOSMALL`, FX buffer shorter than
  150 ms) and "3D must be mono" are checked and logged; streaming/looping sounds use a half-second buffer refilled
  with `Lock`/`Unlock` while polling `GetCurrentPosition` (no `IDirectSoundNotify` use found). FX (`SetFX`, I3DL2
  reverb parameters through `GetObjectInPath`) on buffers with `CTRLFX`.
* DirectInput: `DirectInput8Create(hinst, 0x800, IID_IDirectInput8A)`, keyboard `GetDeviceState(256)`, mouse
  `GetDeviceState(20)` (DIMOUSESTATE2) and `GetDeviceData(cbObjectData 0x14)`, cooperative levels
  `NONEXCLUSIVE|FOREGROUND` / `EXCLUSIVE|FOREGROUND`, data formats passed from the exe's `.rdata` (its own
  c_dfDIKeyboard / c_dfDIMouse2 at `DAT_01100aac`...), joystick enumeration + `SetProperty(DIPROP_DEADZONE)`.
* GUIDs/IIDs are the exe's own copies (`DAT_01100ed4` IID_IDirectInput8A, `DAT_01100fa4` listener IID,
  `g_IID_SndBuffer8`...); the implementation compares by value with the SDK values in the shim headers.

## 2. Declarations

`shim/include/dsound.h`, `dinput.h`, `xinput.h`: SDK vtable order with the slot number before every method,
32-bit layouts (`static_assert` under ILP32: WAVEFORMATEX 18 (packed), DSBUFFERDESC 36, DSCAPS 96, DS3DBUFFER 64,
DS3DLISTENER 64, DSEFFECTDESC 32, DIDATAFORMAT 24, DIOBJECTDATAFORMAT 16, DIDEVICEOBJECTDATA 20, DIDEVICEINSTANCEA
580, DIDEVICEOBJECTINSTANCEA 316, DIMOUSESTATE2 20, DIJOYSTATE2 272, XINPUT_STATE 16, XINPUT_CAPABILITIES 20), the
constants, HRESULTs (sign-correct on LP64 hosts too) and GUIDs used. `IUnknown` shares S6's
`FNVNX_IUNKNOWN_DEFINED` guard. No decomp unit includes these headers (each unit declares its own stand-ins), so
they are checked by a probe TU with the census flags (`-mabi=ilp32 -include fnvnx_compat.h`, together with
`d3d9.h`) and by every platform file under `make -C tests/s7 ilp32`.

## 3. Implementation

### DirectSound (`platform/audio/`)

* `mixer.{h,cpp}` - software mixer: voices read the buffer memory directly (Lock/Unlock hand out pointers), 8/16/
  24/32-bit int and 32-bit float PCM, 1..8 channels (front pair used), linear-interpolation resampling to 48 kHz
  stereo, 32.32 cursors, looping/one-shot (one-shot stops and rewinds to 0 at its end), write cursor = play cursor
  + one output period, position notifications (offsets crossed during a render, `DSBPN_OFFSETSTOP` on Stop / end)
  that `SetEvent` the platform's events. Volume law `10^(mB/2000)`, pan attenuates one side (DirectSound law).
* `ds3d.{h,cpp}` - 3D: inverse-distance with rolloff (`min/(min+rolloff*(d-min))`, clamped at max distance,
  `MUTE3DATMAXDISTANCE`), cones (linear in mB between half-angles), head-relative/disabled modes, attenuate-only
  constant-power pan from the listener-space direction (left-handed, right = top x front), doppler
  `(c - k*vl)/(c - k*vs)`, c = 343.3 m/s / distance factor, clamped [0.5, 2]. No HRTF.
* `dsound.cpp` - IDirectSound8, IDirectSoundBuffer8 (primary: master volume, format request, listener),
  IDirectSound3DBuffer, IDirectSound3DListener (immediate/deferred + CommitDeferredSettings), IDirectSoundNotify,
  DuplicateSoundBuffer (shared data), SetFX/GetObjectInPath with I3DL2/Waves parameter objects (the I3DL2 reverb
  runs in the mixer, platform/audio/reverb.h; Waves: parameters only), parameter validation and HRESULTs as on Windows (CONTROLUNAVAIL without the CTRL flag, BUFFERTOOSMALL,
  PRIOLEVELNEEDED, ...). Software only: no `LOCHARDWARE` buffers.
* `backend.{h,cpp}` - backends `manual` (tests), `null` and `wav:PATH` (host, real-time paced thread);
  `backend_audren.c` - Horizon: audren + audrv, one PCM16 stereo voice, 4 x 512-frame wave buffers (~43 ms).
  Selection `fnvnx_audio_configure()` / `$FNVNX_AUDIO`; started by the first `DirectSoundCreate8`.
* `vorbisfile.cpp` - `ov_open_callbacks, ov_clear, ov_info, ov_seekable, ov_pcm_total, ov_read` (+ time/pcm seek
  and tell): the engine's 0x2D0-byte block stores a magic + pointer to a heap `OggVorbis_File` ("boxed"), so the
  build's libvorbisfile layout does not matter. Real decoding needs `-DFNVNX_HAVE_VORBIS` and the libraries below;
  without them opens fail with `OV_ENOTVORBIS` (the engine then plays no music).

**External dependency (not fetched, not built here):** libogg **1.3.5** and libvorbis **1.3.7** (BSD-3-Clause,
xiph.org release tarballs; record their SHA-256 when the build step fetches them), built as static libraries for
the game's ABI (AArch64, the same data model as `vorbisfile.cpp`), `-DFNVNX_HAVE_VORBIS`, include path
`<vorbis/vorbisfile.h>`. Owner of the fetch/build step: S5 (toolchain).

### Input (`platform/input/`)

* `xinput.cpp` - XInputGetState/SetState/GetCapabilities/Enable over a pad source; `dwPacketNumber` increments on
  change. Mapping (tested table, `fnvnx_pad_map`): face buttons by **label** by default (Nintendo A -> XINPUT A,
  confirm on the right as Switch players expect; `$FNVNX_PAD_LAYOUT=positional` maps by position), L/R ->
  shoulders, ZL/ZR -> triggers 0/255 (Switch triggers are digital), Plus -> START, Minus -> BACK, stick clicks ->
  thumbs, D-pad, sticks unchanged (libnx +-32767, y up). SL/SR unmapped.
* `input_horizon.cpp` - libnx hid: player 1 = Handheld or Npad No1, players 2-4 = No2-No4 (Pro Controller, dual
  Joy-Con, handheld; `padConfigureInput(4, NpadStandard)`); rumble through `hidSendVibrationValues` (low/high
  motor -> amp_low/amp_high, 160/320 Hz); USB keyboard (`hidGetKeyboardStates`, HID usage -> DIK), USB mouse,
  touch screen; pumped at most once per ms by the API calls. LP64 side of the boundary: libnx's C++ headers are
  not ILP32-clean, so this file is checked with `-mabi=lp64`; its interface (`input.h`, `os.h`) is fixed-width C.
* `dinput.cpp` - **decision**: DirectInput exposes the system keyboard and mouse (backed by USB devices and
  touch-as-mouse); game controllers are not enumerated (the gamepad is XInput only, which the engine supports
  fully). Data formats are interpreted generically (object matching by type, instance, GUID, `DIDFT_OPTIONAL`),
  immediate and buffered access (DIPROP_BUFFERSIZE, PEEK, overflow), relative/absolute axis mode, event
  notification, Acquire/Unacquire semantics.
* `keymap.cpp` - USB HID usage -> DIK table; touch-as-mouse: one finger drags the mouse (2 counts/pixel), a tap
  (< 220 ms, < 12 px) clicks left, a two-finger tap clicks right, a two-finger drag scrolls.
* `register.cpp` - registers modules `dsound`, `dinput8`, `xinput1_3` (+ `xinput1_4`, `xinput9_1_0`),
  `vorbisfile` with `fnvnx_register_module`, COM classes CLSID_DirectSound8/DirectSound/DirectInput8 with
  `fnvnx_register_com_class` (static constructor), and exports `fnvnx_audio_input_lookup_export(dll, name)` for
  IAT resolution.

## 4. Tests (`tests/s7`)

`make -C tests/s7` (host, links S4's platform layer for events/modules/COM): 16 tests, 304 checks - volume/pan
math, cursors (one-shot end + rewind, loop wrap, write-cursor lead, frame rounding), resampling and SetFrequency,
mixed sample values (volume, pan, master volume, 8-bit stereo interpolation), Lock regions (wrap,
ENTIREBUFFER, FROMWRITECURSOR), notifications through real events (offsets, wrap, stop, one-shot end), creation
errors and QI rules, duplicates, FX parameter round trip, 3D math (distance, rolloff, max distance, mute,
orientation, head-relative, disable, doppler, cones), the engine's init flow called **through stand-in classes by
slot** (device `Com`, listener `vf3C`/`ApplyColor`/`Commit`), deferred 3D commit, module/COM registration, the
vorbis stub, the XInput mapping table (both layouts), XInput API, DirectInput keyboard/mouse (immediate, buffered,
overflow) with c_dfDIKeyboard/c_dfDIMouse2-shaped formats, touch gestures, HID->DIK. `make -C tests/s7 ilp32`:
syntax check of all S7 sources for AArch64 ILP32 (+ libnx files).

## 5. Open items

* S4: `fnvnx_win32_lookup_export` does not consult modules registered with `fnvnx_register_module`; either it
  falls back to them or the loader (S3) also asks `fnvnx_audio_input_lookup_export`. `imports.tsv` lacks slot
  `0x00fdf438` (vorbisfile `ov_open_callbacks`); confirm all slot names from the player's exe import directory.
* S5: fetch/build libogg 1.3.5 + libvorbis 1.3.7 (pinned above) and define `FNVNX_HAVE_VORBIS`; build the
  Horizon files (`backend_audren.c`, `input_horizon.cpp`) on the LP64 side and decide how ILP32 game code links
  with them.
* S6: Bink audio (`BinkOpenDirectSound` with our IDirectSound8).
* S3: verify the exe's GUID copies (`DAT_01100fa4`, `DAT_01100ed4`, `g_IID_*`) equal the SDK values used here.
* HRTF/surround: not implemented. The I3DL2 reverb for SetFX runs in the mixer per voice (platform/audio/reverb.h:
  room shelf, early taps, a 4-line FDN with decay_time / decay_hf_ratio; SetPreset uses the SDK's I3DL2 presets);
  the Waves reverb keeps its parameters only.
* Text entry (platform/input/swkbd.cpp): L3+R3 together open the system software keyboard (`os_text_entry`); the combination is
  withheld from the game until both clicks are up, a single click is still the game's button. The entered UTF-16 text becomes key
  strokes (Shift down, key down, key up, Shift up), found by inverting the platform's own layout table (`fnvnx_win_vk_char`,
  main block, printable ASCII), released one edge per pump through `fnvnx_swkbd_inject`: the message queue
  (WM_KEYDOWN/UP, WM_CHAR by TranslateMessage, GetAsyncKeyState) and DirectInput (immediate and buffered) from one point.
  Pacing has no time in it: the next edge goes in only after the game observed the previous one, that is a DirectInput
  keyboard read that returned data (either kind) happened after it and its key message was removed by PeekMessageA, and
  only while every acquired keyboard buffer has a free slot (no overflow). With no acquired keyboard or no reads the edges
  stay queued; nothing is dropped or timed out.
  `os_text_entry(max_len, init, init_n, out, n)` is the host service TEXT_ENTRY (hostapi.h, PLATFORM 36; the guest backend is
  os_guest.c): 1 entered, 0 cancelled (nothing typed), negative = -FNVNX_HE_BADINPUT or -(Horizon result). The text is validated
  here: a character no key makes is never dropped, the applet is called again with the refused text as its initial text, until
  the text has strokes or the player cancels (a rule, not a retry count). A negative result ends the process loudly with that
  code; there is no other path to the text. The USB keyboard is not fed into the queue.
* Touch taps press and release in the same pump: visible to buffered reads, not to `GetDeviceState` polling.
* Single sideways Joy-Con not supported; the deadzone is left to the engine.
