# l5-platform-w15-004: declared-machine citation for the 7 constant Galaxy bodies

1. platform/win32/platstat.cpp: `extern "C" void fnvnx_declared_machine(uint32_t id)` beside fnvnx_wine_fixme; writes nothing, returns.
2. verify/declared_machine.tsv: header taken from the trunk (claude/fnv-nx-clean), fact list extended in the same commit with `no-galaxy` (and the source
   list with `galaxy-sdk:<page>:<function>`). Rows 100..106 (ids chosen above anything the trunk table holds; the trunk table had 0 rows):
   SteamAPI_Init, SteamAPI_IsSteamRunning, SteamUser, SteamFriends, SteamUtils, SteamApps, SteamUserStats; fact no-galaxy, source
   galaxy-sdk:partner.steamgames.com/doc/api/<page>:<function> (the Steamworks pages cited in env_const.tsv), kind u32, value 0 (false / NULL).
3. Bodies: gen_env_const.py reads the table by rule (default verify/declared_machine.tsv): a symbol with a row (contract_name == symbol) gets
   `fnvnx_declared_machine(<id>);` first in its body, and its row's value must equal the constant (else the generator fails); a row of the table with no
   body in env_const.tsv fails the full generation. tests/s4/Makefile: the declared table is a prerequisite of the generated includes. No list in code.
4. The 4 stateful entries (Shutdown, RunCallbacks, Register/UnregisterCallback) stay real bodies, cite the SDK at their definitions, have no rows.

## For L1 (verify/objstub.py on the trunk is not changed by me)
- DECL_FACTS = (no-network, time-zone, registry) does not contain `no-galaxy`, and DECL_SOURCE accepts only win-doc: and wine-9.0:. The gate will report
  the 7 rows until L1 adds `no-galaxy` and `galaxy-sdk:` there (the source form here: `galaxy-sdk:<page>:<function>`, page chars [A-Za-z0-9_./#+-]).
- The ids 100..106 are mine; other rows from other L5 workers must pick other ids.
