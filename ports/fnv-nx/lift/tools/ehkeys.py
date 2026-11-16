"""The fixed dispatcher keys of the runtime's own guest entries: the one definition.
A runtime entry that guest code reaches by an address the runtime itself stores (here: the two handlers the msvcrt EH runtime registers on fs:[0], rt/msvcrt_eh.cc) has a fixed
key below every key and image (0x000fffd0.., under the sentinel FNVL_SENT 0x000ffff0). Its body is a guest-state entry of lift/rt (`void f(void)`), so it is reached through a
bridge like every entry row: tools/impgen.py emits the bridge thunk I_<key> for each row here (the whole x86 return from conv and args), and the pack's entries.tsv takes the row from
impgen's output as kind import. Nothing else lists these keys; rt/msvcrt.h spells the same keys as the macros FNVL_EH_KEY_* that rt/msvcrt_eh.cc stores into its registrations, and
tools/tests/test_ehkeys.py compares that spelling with this table.
Rows: (key, definer symbol in lift/rt, conv, args) with conv and args as in the bridge table (cdecl, four stack words: rec, frame, ctx, dispatch)."""
EH_KEYS = (
    (0x000fffd0, 'fnvl_msvcrt_eh_nested_catch', 'cdecl', 'iiii'),     # Wine catch_function_nested_handler
    (0x000fffe0, 'fnvl_msvcrt_eh_nested_collide', 'cdecl', 'iiii'),   # Wine MSVCRT_nested_handler
)
