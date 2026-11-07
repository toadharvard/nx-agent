# Scenario bench (S18, D25)

The 12 zero targets of D25 (docs/ARCHITECTURE.md) are measured on scripted scenarios. Each scenario runs with the same
deterministic input on two machines: the player's FalloutNV.exe under Wine (the S16 sweep's prefix) and the guest in
the emulator (tests/s5/emu_guest.py). One report per scenario, one metric over all of them.

| file | what |
|---|---|
| `scenarios.json` | the scenarios: boot-menu, newgame, cells, saveload, quest (macros: console, type, save, load, state, cells, quest) |
| `scenario.py` | `compile` (macros -> press / wait / until / mark), `validate` (ESM ids, keymap), `seed` (the guest's profile inis), `drive-wine`, `report`, `metric` |
| `keys.py` | key names (xdotool keysyms) -> HID usages; text -> key presses |
| `emu_scenario.py` | the guest side: emu_guest.py with the plan fed through the host API's `input_poll` on virtual time |
| `wine_scenario.sh` | the original side: wine_ref.sh setup, optional fnvtrace (lockstep), the driver, then saves and the console log |
| `savediff.py` | the save round trip: header and screenshot left out, content-defined chunks, anchor-keyed regions |
| `optional_files.txt` | files the original does without (not counted as failed opens) |
| `../farm/scenario_job.sh` | the farm job: every scenario, both sides, compare, threads lane, report, metric, private publish |

## Determinism

- Input: keys only, the same names on both sides. The guest gets FNVNX_EV_KEY events (HID usages, mapped to DIK codes
  by platform/input/keymap.cpp) on the emulator's virtual clock; Wine gets xdotool presses on wall time.
- Gates: `until milestone` waits for a boot milestone on the guest (tests/s5/milestones.txt) and is a fixed wait
  (`wine_s`) on the original; `until file` (saves) is polled on both.
- Ids: cells are checked against FalloutNV.esm (`validate --esm`); the quest is found by its in-game title, and its
  stages are read from the ESM, at compile time on the farm.
- Inis: tools/compare/ref_ini.py for both (no intro movies, windowed, no threaded AI, no audio).

## Report

`scenario.py report` gives each target `{measured, count, detail}`; the md copy has counts and scalar details only
(game text stays in the private json). See the docstring of `scenario.py` for what each target counts.

## Local

    python3 -I tests/s18/test_scenario.py          # synthetic: no game, no guest, no Wine
    tools/scenario/scenario.py compile saveload -o plan.json
    FARM_BOOTS=stub EMU_PY=tools/scenario/emu_scenario.py EMU_EXTRA="--scenario plan.json --scenario-out out" \
        tests/s5/farm_boot.sh <link12> out
    tools/scenario/scenario.py report plan.json --guest out --out r.json --md r.md
