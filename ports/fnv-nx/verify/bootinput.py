#!/usr/bin/env python3
"""The input and mode model of the boot machine, rule R-INPUT (journal/inbox/l4-verify-011.md).

A run declares DEVICE STATES over the machine's virtual time (a scenario, schema "fnvnx-scenario/1"); the model reads them exactly as the
console host does (toolchain/host/host_v3.inc, L5 e873bf74; the line numbers below are that file's). The host reads STATES, not an event queue:
  state(dev, T) = the last declared state of dev with t_ns <= T (equal t_ns: file order); before the first, the declared machine's default:
                  no pad connected, no keys, no mouse buttons, mouse not connected, no touch point, docked = the --profile.
  pad_read(i)   host_v3.inc:115-131: index > 3: 0, nothing written; else 32 bytes {u32 connected, u32 0, u64 buttons, s32 lx ly rx ry}, all
                zero when not connected; returns 1 when connected.
  input_poll    host_v3.inc:165-197, with its own g_prev_kbd / g_prev_mouse_buttons kept here; events cut at cap as PUSH does (:168): an
                event beyond cap is lost, the state (g_prev_*) still advances.
  pad_vibrate   host_v3.inc:133-163: index > 3: 0, nothing done; else recorded as an event; returns 0.
Each declared state is one HID sample: a state shorter than the gap between two polls is not seen, and the same mouse sample read twice gives its
delta twice (host_v3.inc:181 reads the current sample, :186 only keeps the buttons). That is the host's behaviour, not smoothed.
A value outside the host's type range is a ScenarioError (never clamped)."""
import bisect
import hashlib
import json
import struct

SCHEMA = "fnvnx-scenario/1"
U64, S32, U32 = (1 << 64) - 1, (-(1 << 31), (1 << 31) - 1), (1 << 32) - 1
EV_KEY, EV_MOUSE_MOVE, EV_MOUSE_BUTTON, EV_TOUCH, EV_TOUCH_END = 1, 2, 3, 4, 5   # hostapi.h, input_poll events
EXPECT_KEYS = {"kind", "tid", "t_ns", "index", "lo", "hi"}
FIELDS = {   # dev -> (required, optional with its default)
    "pad": ({"index"}, {"connected": 0, "buttons": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}),
    "kbd": (set(), {"keys": []}),
    "mouse": (set(), {"connected": 0, "buttons": 0, "dx": 0, "dy": 0, "wheel": 0}),
    "touch": (set(), {"points": []}),
    "docked": ({"value"}, {}),
}


class ScenarioError(Exception):
    pass


def _int(v, what, lo, hi):
    if isinstance(v, bool) or not isinstance(v, int):
        raise ScenarioError("%s must be an integer, got %r" % (what, v))
    if not lo <= v <= hi:
        raise ScenarioError("%s = %d is outside %d..%d" % (what, v, lo, hi))
    return v


def _keys(d, allowed, what):
    extra = sorted(set(d) - allowed)
    if extra:
        raise ScenarioError("%s: unknown key %s" % (what, ", ".join(map(repr, extra))))


class Scenario:
    """parse()d scenario: .text_entries (None when the key is absent), .states {(dev, index): [(t_ns, state)]} stable-sorted, .expect, .sha256, .text"""

    def __init__(self, text):
        self.text = text
        self.sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        try:
            doc = json.loads(text)
        except ValueError as e:
            raise ScenarioError("not JSON: %s" % e) from None
        if not isinstance(doc, dict):
            raise ScenarioError("the scenario must be a JSON object")
        _keys(doc, {"schema", "text_entries", "states", "expect_events"}, "scenario")
        if doc.get("schema") != SCHEMA:
            raise ScenarioError("schema must be %r, got %r" % (SCHEMA, doc.get("schema")))
        te = doc.get("text_entries")
        if te is not None and (not isinstance(te, list) or not all(isinstance(t, str) for t in te)):
            raise ScenarioError("text_entries must be a list of strings")
        self.text_entries = te
        states = doc.get("states", [])
        if not isinstance(states, list):
            raise ScenarioError("states must be a list")
        rows = []
        for n, s in enumerate(states):
            what = "states[%d]" % n
            if not isinstance(s, dict):
                raise ScenarioError("%s must be an object" % what)
            dev = s.get("dev")
            if dev not in FIELDS:
                raise ScenarioError("%s: dev must be one of %s, got %r" % (what, "/".join(sorted(FIELDS)), dev))
            req, opt = FIELDS[dev]
            _keys(s, {"t_ns", "dev"} | req | set(opt), what)
            for k in sorted(req | {"t_ns"}):
                if k not in s:
                    raise ScenarioError("%s: missing key %r" % (what, k))
            t = _int(s["t_ns"], what + ".t_ns", 0, (1 << 63) - 1)
            st = dict(opt, **{k: v for k, v in s.items() if k not in ("t_ns", "dev", "index")})
            idx = 0
            if dev == "pad":
                idx = _int(s["index"], what + ".index", 0, 3)
                st["connected"] = _int(st["connected"], what + ".connected", 0, 1)
                st["buttons"] = _int(st["buttons"], what + ".buttons", 0, U64)
                for k in ("lx", "ly", "rx", "ry"):
                    st[k] = _int(st[k], "%s.%s" % (what, k), *S32)
            elif dev == "kbd":
                if not isinstance(st["keys"], list):
                    raise ScenarioError("%s.keys must be a list" % what)
                st["keys"] = frozenset(_int(k, what + ".keys[]", 4, 231) for k in st["keys"])
            elif dev == "mouse":
                st["connected"] = _int(st["connected"], what + ".connected", 0, 1)
                st["buttons"] = _int(st["buttons"], what + ".buttons", 0, 0x1F)   # buttons 0..4 (host_v3.inc:182)
                for k in ("dx", "dy", "wheel"):
                    st[k] = _int(st[k], "%s.%s" % (what, k), *S32)
            elif dev == "touch":
                pts = st["points"]
                if not isinstance(pts, list) or len(pts) > 16:
                    raise ScenarioError("%s.points must be a list of at most 16 points" % what)
                out = []
                for i, p in enumerate(pts):
                    pw = "%s.points[%d]" % (what, i)
                    if not isinstance(p, dict):
                        raise ScenarioError("%s must be an object" % pw)
                    _keys(p, {"id", "x", "y"}, pw)
                    for k in ("id", "x", "y"):
                        if k not in p:
                            raise ScenarioError("%s: missing key %r" % (pw, k))
                    out.append((_int(p["id"], pw + ".id", 0, U32), _int(p["x"], pw + ".x", *S32), _int(p["y"], pw + ".y", *S32)))
                st["points"] = tuple(out)
            else:
                st["value"] = _int(st["value"], what + ".value", 0, 1)
            rows.append((t, dev, idx, st))
        rows.sort(key=lambda r: r[0])   # stable: equal t_ns keep the file order
        self.states = {}
        for t, dev, idx, st in rows:
            self.states.setdefault((dev, idx), []).append((t, st))
        self.expect = doc.get("expect_events", [])
        if not isinstance(self.expect, list):
            raise ScenarioError("expect_events must be a list")
        for n, e in enumerate(self.expect):
            if not isinstance(e, dict) or e.get("kind") != "pad-vibrate":
                raise ScenarioError("expect_events[%d] must be an object of kind 'pad-vibrate'" % n)
            _keys(e, EXPECT_KEYS, "expect_events[%d]" % n)
            for k in ("lo", "hi"):
                if k in e and not isinstance(e[k], str):
                    raise ScenarioError("expect_events[%d].%s must be a hex string" % (n, k))

    def state(self, dev, idx, t, default):
        """the last declared state of (dev, idx) with t_ns <= T, else DEFAULT"""
        lst = self.states.get((dev, idx))
        if lst:
            i = bisect.bisect_right([x[0] for x in lst], t)
            if i:
                return lst[i - 1][1]
        return default

    def check_expect(self, events):
        """compare expect_events, in order, with the recorded pad-vibrate events -> [] or the differences"""
        got = [e for e in events if e["kind"] == "pad-vibrate"]
        diff = []
        if len(got) != len(self.expect):
            diff.append("expected %d pad-vibrate events, recorded %d" % (len(self.expect), len(got)))
        for i, (x, g) in enumerate(zip(self.expect, got)):
            for k, v in x.items():
                if g.get(k) != v:
                    diff.append("event %d: %s expected %r, recorded %r" % (i, k, v, g.get(k)))
        return diff


NO_PAD = {"connected": 0, "buttons": 0, "lx": 0, "ly": 0, "rx": 0, "ry": 0}
NO_KBD = {"keys": frozenset()}
NO_MOUSE = {"connected": 0, "buttons": 0, "dx": 0, "dy": 0, "wheel": 0}
NO_TOUCH = {"points": ()}


class InputModel:
    """the host's input services over a Scenario (None: the declared machine with nothing attached)"""

    def __init__(self, scenario=None, watch_reads=False):
        self.sc, self.watch = scenario, watch_reads
        self.prev_keys, self.prev_mouse_buttons = frozenset(), 0   # g_prev_kbd (host_v3.inc:92), g_prev_mouse_buttons (:93)

    def _state(self, m, dev, idx, default):
        t = m.clock.now_ns()
        if self.watch:
            m._event("input-read", dev=dev, t_ns=t)
        return self.sc.state(dev, idx, t, default) if self.sc else default

    def docked(self, m, profile_docked):
        """DISPLAY_DOCKED at T = state(docked, T), default --profile"""
        return self._state(m, "docked", 0, {"value": 1 if profile_docked else 0})["value"]

    def pad_read(self, m, i, out):
        if i > 3:                                           # host_v3.inc:116
            return 0
        p = self._state(m, "pad", i, NO_PAD)
        if p["connected"]:                                  # :123-128; not connected: zeros (:122)
            m.uc.mem_write(out, struct.pack("<IIQiiii", 1, 0, p["buttons"], p["lx"], p["ly"], p["rx"], p["ry"]))
            return 1
        m.uc.mem_write(out, bytes(32))
        return 0

    def pad_vibrate(self, m, i, lo, hi):
        if i <= 3:                                          # :134
            m._event("pad-vibrate", t_ns=m.clock.now_ns(), index=i, lo="0x%x" % (lo & U32), hi="0x%x" % (hi & U32))
        return 0

    def input_poll(self, m, out, cap):
        ev = []

        def push(t, c, a, b, cc):                           # PUSH (:168): cut at cap, the rest is lost
            if len(ev) < cap:
                ev.append(struct.pack("<IIiiiI", t, c, a, b, cc, 0))
        k = self._state(m, "kbd", 0, NO_KBD)["keys"]        # :171-178, hidGetKeyboardStates returns 1 sample
        for u in range(4, 232):
            now, was = u in k, u in self.prev_keys
            if now != was:
                push(EV_KEY, u, int(now), 0, 0)
        self.prev_keys = k
        ms = self._state(m, "mouse", 0, NO_MOUSE)           # :179-187
        if ms["connected"]:
            if ms["dx"] or ms["dy"] or ms["wheel"]:
                push(EV_MOUSE_MOVE, 0, ms["dx"], ms["dy"], ms["wheel"])
            for b in range(5):
                now, was = (ms["buttons"] >> b) & 1, (self.prev_mouse_buttons >> b) & 1
                if now != was:
                    push(EV_MOUSE_BUTTON, b, now, 0, 0)
            self.prev_mouse_buttons = ms["buttons"]
        ts = self._state(m, "touch", 0, NO_TOUCH)["points"]  # :188-193
        c = min(len(ts), 16)
        for fid, x, y in ts[:c]:
            push(EV_TOUCH, fid, x, y, 0)
        push(EV_TOUCH_END, c, 0, 0, 0)
        if ev:
            m.uc.mem_write(out, b"".join(ev))
        return len(ev)
