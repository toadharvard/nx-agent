# L4 VERIFY 011: input and mode scenario of the shared model, rule R-INPUT (item 15, 2026-11-12 03:30Z)

Class: the boot machine answers `pad_read` (no pad), `input_poll` (0), `pad_vibrate` (0) and DISPLAY_DOCKED (fixed by
--profile) with the declared machine's "nothing attached" state only. L5's host tests (22 tests, L5 w9
`platform/manifest/doubles_input_needs.tsv` @b8847f6a) inject input through test doubles. With the doubles removed they need
the input from the one shared model.

## Rule R-INPUT: a run declares DEVICE STATES over virtual time; the model reads them exactly as the console host does

The console host (toolchain/host/host_v3.inc, L5 @e873bf74) reads STATES, not an event queue:
- `pad_read(i)` returns the current pad state: connected, buttons, sticks;
- `input_poll` reads the current keyboard, mouse and touch states and emits the DIFFERENCE from what the previous
  poll read: key edges (usages 4..231), the mouse's current delta (when non-zero) and button edges (0..4), and every
  current touch point plus one TOUCH_END(count) on every poll (count clamped to 16, events cut at cap).

So the scenario is a timeline of device states, and the model is host_v3.inc's code run over it:
- `state(dev, T)` = the last declared state of `dev` with `t_ns <= T` (T = the machine's virtual clock at the call); before the
  first one, the declared machine's default: no pad connected, no keys, no buttons, no touch, mouse not connected, docked =
  --profile;
- `pad_read(i)` at T: `state(pad i, T)` (connected 0 writes zeros, as the host does);
- `input_poll(out, cap)` at T: the host's algorithm, with `HidKeyboardState`/`HidMouseState`/`HidTouchScreenState` =
  `state(kbd|mouse|touch, T)` and its own `g_prev_*` kept by the model, cut at cap exactly as PUSH does (events beyond cap are lost,
  as on the console);
- DISPLAY_DOCKED at T: `state(docked, T)`;
- `pad_vibrate(i, lo, hi)`: recorded as an event {kind "pad-vibrate", tid, t_ns, index, lo, hi} (float bits as given, index > 3
  recorded as the host ignores it: no event). It returns 0, as the host does.

Each declared state is one HID sample. A state shorter than the gap between two polls is not seen, and the same mouse
sample read twice gives its delta twice. That is the host's own behaviour, reproduced and not smoothed. Time-based delivery
makes a test independent of the polling rate by construction: swkbd_polling_rate_independent declares strokes in time, not
per poll.

## Format: `boot.py --scenario FILE` (JSON, schema "fnvnx-scenario/1")

```
{"schema": "fnvnx-scenario/1",
 "text_entries": ["..."],                                   # as --text-entry (rule 5d); both given = error
 "states": [                                                # sorted by t_ns (equal t_ns: file order); unknown key = error
   {"t_ns": 0,        "dev": "pad",   "index": 0, "connected": 1, "buttons": 16, "lx": 0, "ly": 0, "rx": 0, "ry": 0},
   {"t_ns": 1000000,  "dev": "kbd",   "keys": [4, 225]},       # USB HID usages held down
   {"t_ns": 2000000,  "dev": "mouse", "connected": 1, "buttons": 1, "dx": 3, "dy": -2, "wheel": 0},
   {"t_ns": 3000000,  "dev": "touch", "points": [{"id": 0, "x": 640, "y": 360}]},
   {"t_ns": 4000000,  "dev": "docked", "value": 1}],
 "expect_events": [ {"kind": "pad-vibrate", "index": 0, "lo": "0x3f000000", "hi": "0x0"} ]}
```

- Ranges are checked against the host's types (buttons u64, sticks s32, keys 4..231, mouse buttons 0..4, at most 16 points, x/y
  s32). A value outside its range is a scenario error, and the run ends as "scenario-error". It is never clamped.
- A guest test advances virtual time with its own sleep_ns (or waits) between its phases: it declares the state at t, sleeps past
  t, reads, and asserts. Assertions about what the PLATFORM SENT to a device (vibration) cannot be made inside the guest without a
  test-only host service, which would be a double. They are `expect_events`: the runner compares them, in order, with the run's
  recorded events of that kind. A difference is a test failure.
- OUT.json `inputs` records the scenario file's sha256 and content. Every model read is an event {kind "input-read", dev, t_ns}
  only under --watch-log's existing switch (not by default, to keep the record small).

## Check that proves it
tests/test_bootmachine.py cases per rule line, with the expected values derived by hand from host_v3.inc's code: before the first
state; edges; a missed short stroke; the mouse delta read twice; touch END every poll; cap cut; a docked change mid-run; vibrate
recorded; each range error. Then L5's 22 tests rewritten on the scenario (L5 w9), green on the boot machine, with 0 input
doubles. Plus the L1 verdict.

Owner: L4 (the model, a worker), L5 (the 22 tests). After item 11 step B.
