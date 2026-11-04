### J-XXXX | 2026-11-04 | stream: V6n-render | status: wip | parent: J-XXXX(render-005)

* **did** - V6n checked C77 (`NxOp_Marker`) on its own (`c60677b`):
  - Property test `tests/v6n/claims/c77_markers.cpp`: 3000 random frames (51,316 draws, 27,002 markers, 3,099
    untagged passes) go through `nx::Frame::Finish` into a `CaptureBackend`, and the test parses the stream itself.
    It checks that every draw sits under its own tag or none, that there is one marker per run, no marker in a pass
    without tagged draws and none outside a pass, and that the capture holds the words verbatim. Three mutants of
    `front.cpp` each fail it.
  - `render/verify/capture/nxcap.py inject` puts a marker before each of the 160 draws of the N0 capture, and
    `replay_gl` shows 0 read-back differences. deko3d skipping the op was checked by source inspection only.
  - `nxcap.py markers FILE --passes build/v6n/pass_space.tsv` joins draws per (pass id, family) with pass
    name/class/emit kind and exits non-zero on an id > 758. It is tested on synthetic and injected captures only.
* **found** - `Frame::Tag` / `SetEnginePass` accept any 16-bit id, including > 758 and 0. No caller passes those, so
  this is not filed as a defect; the join tool flags such ids.
* **verified** - Re-run by the render machine at `c60677b` from an empty `build/v6n`: `tests/v6n/run.sh` rc 0,
  "C77 markers: ... PASS", "selfcheck PASS, open defects: none".
* **metrics** - c77_frames=3000 c77_draws=51316 c77_markers=27002 c77_mutants_caught=3/3 injected_replay_diffs=0
* **next** - Real per-pass counts need engine-side families that call `SetEnginePass` (N1 wiring, waiting on R2).
