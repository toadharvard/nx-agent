# The original's boot from masters-loaded to first-cell (S18)

What the player's FalloutNV.exe 1.4.0.525 does under Wine, traced at function boundaries (tools/lockstep/fnvtrace,
681 functions of the address map plus kernel32!CreateFileA and kernel32!CreateThread, the hottest ~80 functions left
out), over boot -> main menu -> console `coc GSDocMitchellHouse` -> the player in the cell (screenshot-confirmed).
Run with tools/scenario/wine_scenario.sh (3 passes, the last one complete). The full timeline with paths is private
(fnv-nx-build branch `scenario/original`, timeline.md); this page has exe addresses, the decomp's names and file
classes only. Section "Milestone markers" is what tests/s5/milestones.txt should detect (S5-boot).


## A. Phases
P0 pre-WinMain (seq 1-1011): 6 pool threads + 1 WaitAndExitThread started by FUN_00b01d50 ctor.
P1 startup (1012-3078):
- 0x0086a850 WinMain @1012 n=1 (WinMain 0x0086a850 is traced: seq 1012, one call).
- file classes in order: ini/plugin list (relative, FAIL), 6 archives (bsa: Fallout - Textures, Textures2, Meshes, Voices1, Sound, Misc; ok), other (renderer info, written twice), shader package (ok).
- renderer: 0x00e81940 NiD3DShaderInterface::InitializeShaderRenderer(GraphicsRenderer*) @1555, 0x00e88eb0 NiD3DGeometryGroupManager::Create(...) @1556; scene graph 0x004dc650 Main::CreateMenuSceneGraph() @2364; UI 0x00702250 Interface::Init(bool) @2405, 0x0070a130 InterfaceManager_FUN_0070a130::InterfaceManager_FUN_0070a130() @2407, 0x0070adb0 FUN_0070adb0_shell::InitInterface(bool) @2425.
- loading screen: 0x00788cc0 CreateLoadingMenuTile(TESForm*, bool, bool) @2456, 0x007890e0 FUN_007890e0_shell::InitLoadingMenu(TESForm*) @2918, 0x0078d2f0 LoadingMenuThread::LoadingMenuThread() @2942 (LoadingMenuThread runs on its own tid 352 from @2951).
- 0x00639d40 Sky::Sky() @2961, 0x004ba5e0 GridCellArray::Init() @3049, 0x00c3ee70 BSTaskManagerThread::BSTaskManagerThread(void*, unsigned int) @3064.
P2 data load (3079-340462, main thread):
- 0x00462f40 TESDataHandler::LookupTESFile(char const*) @3079 n=4, then esm open (FalloutNV.esm, ok) @3083, 0x0046feb0 TESDataHandler::GetDLCIndex(char const*) @3091, bsa open (Update.bsa, ok) @3098, 0x00847480 CreateSaveLoadGame() @3105, ini/plugin list (ok) @3165.
- 0x00851d10 BGSSaveLoadManager::UpdateSaveTimestamp() @3372, 0x0093a5f0 PlayerCharacter::SetUnderAIControl(bool) @3373, 0x00455200 TES_ExteriorBuffer::FUN_00455200(bool, TESWorldSpace*) @3400, 0x0046a370 TESDataHandler::InitializeMagicSystem() @3446, 0x004650a0 TESDataHandler::SetSaveLoadGameState(int) @3452 (n=3).
- the record parsing itself runs in untraced hot functions; main thread owns it (840k of 972k calls).
- music file open (class music, main-title) @18299 on tid 324 (non-main): overlaps the load.
- end: 0x0046fb50 TESDataHandler::MarkGameSettingsLoaded() @340463 n=1.
P3 post-load setup (340463-369572):
- 0x004617e0 TESDataHandler::GetAddonNodeByIndex(unsigned int) @340465 n=37; texture/model queues start: 0x00443af0 TextureLoader::QueueTextureLoad(BSFileEntry*, int, QueuedFile*) @340573, 0x00442600 ModelLoader::ProcessQueuedFile() @340577; archive-read worker tid 364 starts @340579.
- body part / impact / texture-set / weapon resource queues: 0x005e5590 BGSBodyPartData::QueueResourceLoad(IO_TASK_PRIORITY, QueuedFile*) @342789, 0x0058ef10 BGSImpactDataSet::QueueResourceLoad(int, QueuedFile*) @343005, 0x005930e0 BGSTextureSet::LoadTextures(int, QueuedFile*, bool) @343018, 0x00525510 TESObjectWEAP::QueueResourceLoad(int, QueuedFile*, bool) @352095.
- BSThread started @360011 runs 0x00462ee0 TESDataHandler::BuildCompiledFileList() @360016 (tid 392) and re-opens FalloutNV.esm (esm, ok) @360031.
- 0x007043c0 Interface::IsWorldSpaceEnabled(TESWorldSpace*) @360307, 0x00587ff0 TESWorldSpace::AddPersistentReference(TESObjectREFR*) @361735, 0x004614e0 TESDataHandler::GetRegions() @367264, 0x007022c0 Interface::RefreshInterfaceState() @367558; HUD built: 0x0076bdc0 HUDMainMenu::GetClass() @368196 (n=6), 0x0076bdd0 HUDMainMenu::AssignTileByID(int, Tile*) @368198 (n=54).
P4 main menu built (369573-371148):
- 0x007cb7d0 InitStartMenu(bool, bool) @369573, 0x007ce6b0 ClearStartMenuEntries() @369575, 0x007cb4d0 StartMenu::GetClass() @369974 (n=6), 0x007cbaf0 StartMenuInit::FUN_007cbaf0(bool, bool) @370593, 0x007cc5d0 ListBox<StartMenu::Option*>::AddItem(unsigned int) @370609 (n=39, @370609-370648), 0x00850fe0 BGSSaveLoadManager::CanSave(bool) @370611.
- one-shots 0x007160b0 FUN_007160b0(InterfaceManager*) @371140, 0x004431d0 ModelLoader::FUN_004431d0() @371145, 0x00443220 ModelLoader::FUN_00443220() @371146. 0x007cb330 (StartMenu ctor) is NOT in the traced list.
P5 menu frame loop (371149-602313): 10061 menu frames.
- per-frame: 0x00851d90 BGSSaveLoadManager::ProcessQueuedSaveLoad() @371149, 0x0070b8f0 FUN_0070b8f0_shell::UpdateStartMenuState() @371154, 0x0070c4a0 FUN_0070c4a0_shell::UpdateInterface() @371155, 0x007d5830 SMStartMenu::FUN_007d5830() @382927, 0x0086ff70 Main86::FUN_0086ff70() @384026 (the frame counter, n=11081).
- one-shots in the first frames: start-menu option sync 0x007d3170.. (@385714-385730, n=1 each), 0x00709c40 Interface::SetHUDVisible(bool) @386027, 0x00af8f10 ArchiveNames::LoadNames() @386305 (n=7).
P6 console handling (602314-613942), inside menu frames 9554 and 10061:
- other file (console output) open FAIL then ok x2 @602317-602319 (SetConsoleOutputFilename), 0x005ae670 TESDataHandler::ClearHairList() @602314.
- last menu input call 0x007d5830 SMStartMenu::FUN_007d5830() @613902; other file (console output) ok @613928 (coc); ClearHairList called again @613936.
P7 cell load (613943-695969), one frame call (no frame counter call from @615915 to @695970):
- 0x0054cfd0 TESObjectCELL_Ext::FUN_0054cfd0(NiPoint3*, NiPoint3*) @613943, 0x0054de40 FUN_0054de40(TESObjectCELL*) @613946, 0x00454bd0 TES::ContainsCell(TESObjectCELL*) @616330, loading screen shown: 0x00788cc0 CreateLoadingMenuTile(TESForm*, bool, bool) second call @616337.
- 0x00849b10 BGSSaveLoadGame::FormReconstructionMap(TESObjectCELL*) @616701, 0x00852d20 BGSSaveLoadReferencesMap::LoadCellReferences(...) @616702, 0x00628da0 LoadedAreaBound::FUN_00628da0() @616718, 0x00453490 FUN_00453490_shell::FUN_00453490(TESObjectCELL*) @616747, 0x00455400 FUN_00455400_tes::UpdateSkyModeForCell(TESObjectCELL*) @621191, 0x0063a3f0 Sky::SetMode(Sky__SKY_MODE) @621193.
- IO and loader work: 0x0043d690 IOManager::SubmitTask() @616775, 0x00477640 BGSDestructibleObjectForm::QueueResourceLoad(...) @616922, 0x00950b00 PlayerCharacter::FUN_00950b00(bool) @620649, 0x00446500 ModelLoader::QueueFileList(...) @621042, 0x004b4f00 FUN_004b4f00(TESDataHandler*) @655661, 0x00547110 TESObjectCELL::GetRegionList(bool) @655685, 0x00947d10 TESObjectCELL::ClearAudioMarkers() @655686.
- worker tids: 364 (archive reads, 40084 calls), 368 (first call @617466, 5344 calls), 352 (loading screen). No thread is created and no file is opened in this phase (assets come from the open archives).
P8 cell frames (695970-end), 1019 frames:
- 0x00952ba0 PlayerCharacterStandIn::FUN_00952ba0() @691939 (n=1020), 0x00950290 PlayerCharacter::UpdateCameraMode() @691975 (n=1018), 0x00875110 SetRenderCamera::Main::FUN_00875110(...) @697075.
- HUD: 0x00778b10 HUDMainMenu::RefreshTargetDisplay() @696109 (n=3); 0x009520f0 PlayerCharacter::SetActionGroupFlags(unsigned short, ACTION_FLAGS) @698578, 0x00627980 BoundaryOwner::BuildWalls(TESObjectCELL*, BoundaryBox const*) @698696 (n=1 each).
- music files (class music): 29 opens @698096-969272, alternating tids 324/328, ~10 frames apart.

## B. Threads (114 CreateThread calls)
| start | creator | count | phase |
|---|---|---|---|
| FUN_00b023f0 | FUN_00b01d50 ctor | 6 | P0 |
| WaitAndExitThread | FUN_00b01d50 ctor | 1 | P0 |
| RunThreadProc (BSThread) | BSThread::Initialize | 6: @1014, @1543, @1544 (P1), @2943 LoadingMenuThread (P1), @3078 (P1), @360011 BuildCompiledFileList (P3) | P1, P3 |
| FUN_00ec16b0 | BEC16D0MoviePlayerThread::FUN_00ec16d0 | 1 | P1 |
| FUN_00c42da0 | FUN_00c42dd0 ctor (BSTaskManagerThread) | 2 | P1 |
| system DLL threads (raw addresses) | system DLL | 4+1+1+1 in P1; 30 (audio, 749fb990) + 60 (audio, 744abcc0) + 1 in P2/P8 | P1, P2, P8 |
No thread starts during P4, P5, P6 or P7.

## C. Files (50 opens): classes
- startup: 1 pipe (system), 1 ini/plugin list FAIL, 6 bsa ok, 2 other ok (renderer info), 1 shader package ok, 1 pipe (system, tid 340).
- data load: esm FalloutNV.esm ok @3083, bsa Update.bsa ok @3098, ini/plugin list ok @3165, music ok @18299.
- post-load: esm FalloutNV.esm ok @360031 (tid 392).
- console: other, 1 FAIL + 3 ok @602317-602319 and @613928.
- cell frames: music, 29 ok. Failed opens in total: 2.

## D. Milestone markers
- masters-loaded: latest one-shot 0x0046fb50 TESDataHandler::MarkGameSettingsLoaded() @340463 tid 204. Earlier: 0x00462f40 TESDataHandler::LookupTESFile(char const*) @3079, 0x00847480 CreateSaveLoadGame() @3105, 0x004650a0 TESDataHandler::SetSaveLoadGameState(int) @3452. The ESM re-open @360031 is later and not part of it.
- main-menu: StartMenu ctor 0x007cb330 not traced. Use 0x007cbaf0 StartMenuInit::FUN_007cbaf0(bool, bool) @370593 or the last one-shot 0x007160b0 FUN_007160b0(InterfaceManager*) @371140; menu start 0x007cb7d0 InitStartMenu(bool, bool) @369573.
- first-frame: Present not traced. Frame loop head 0x00851d90 BGSSaveLoadManager::ProcessQueuedSaveLoad() @371149; frame counter 0x0086ff70 Main86::FUN_0086ff70() @384026; first menu-settled one-shot 0x00709c40 Interface::SetHUDVisible(bool) @386027.
- first-cell: 0x00852d20 BGSSaveLoadReferencesMap::LoadCellReferences(...) @616702 (cell attached); in-cell frames 0x00952ba0 PlayerCharacterStandIn::FUN_00952ba0() @691939, 0x00950290 PlayerCharacter::UpdateCameraMode() @691975; HUD 0x00778b10 HUDMainMenu::RefreshTargetDisplay() @696109; last cell one-shot 0x00627980 BoundaryOwner::BuildWalls(TESObjectCELL*, BoundaryBox const*) @698696.
- WinMain 0x0086a850: seq 1012, tid 204, n=1.

## E. Surprises
- Renderer and loading screen (own thread) come up before the data load; archives before the renderer.
- Main-title music is opened on a non-main thread in the middle of the data load.
- coc runs inside the live menu frame loop; the whole cell load is one frame call, with no thread or file opened.
- A BSThread re-opens the ESM after the load (BuildCompiledFileList); order vs. other post-load calls varies by a thread race between runs.
- 11081 frames: 10061 in the menu, 1019 in the cell.

## Milestone markers (for tests/s5/milestones.txt; honest = fires only in the state the original is in)

Order in the original (prerequisites; a hit before its prerequisite is "early", not reached):
winmain < ini-read < archive-list < bsa-opened < renderer-init < esm-header < masters-loaded < main-menu < first-frame
< first-cell. Note renderer-init comes after the archives, not before (the table's line order differs).

| milestone | marker in the original | detect in the emulator |
|---|---|---|
| winmain | 0x0086a850 entered once, seq 1012, after 7 pool threads (0x00b023f0 x6, WaitAndExitThread, created by 0x00b01d50's constructor) | unchanged (`call 0x0086a850`) |
| ini-read | Fallout_default.ini / Fallout.ini read through the ini reader before any archive | a read at offset 0 of an ini after winmain |
| archive-list | the relative Plugins.txt probe fails, then the 6 SArchiveList archives open in list order | the first *open* (not stat) of the list's first entry |
| bsa-opened | all 6 list archives open (Textures, Textures2, Meshes, Voices1, Sound, Misc); Update.bsa is NOT in this set (it opens later, after the ESM) | every list entry opened and its `BSA\0` header read; Update.bsa not required |
| renderer-init | right after the archives: renderer info written, 0x00e81940 NiD3DShaderInterface::InitializeShaderRenderer, the shader package, 0x00702250 Interface::Init, the loading screen 0x0078d2f0 LoadingMenuThread (own thread) | CreateDevice ok AND the engine's renderer init returns (0x00e72e60 InitRenderer, callret) AND 0x00e81940 entered |
| esm-header | FalloutNV.esm opens after the loading screen thread starts | TES4 read at offset 0 after renderer-init |
| masters-loaded | 0x0046fb50 TESDataHandler::MarkGameSettingsLoaded, one call, the last one-shot of the data load (seq 340463) | 0x0046fb50 entered and returned, AND >= 99 % of each master's bytes read (coverage, not the furthest offset) |
| main-menu | 0x007cb7d0 InitStartMenu, 0x007cbaf0 StartMenuInit, 39 x 0x007cc5d0 ListBox<StartMenu::Option>::AddItem, then the one-shot 0x007160b0 (seq 371140) | 0x007cbaf0 returned AND >= 1 AddItem AND 0x007160b0 entered, after masters-loaded |
| first-frame | the main loop: 0x00851d90 BGSSaveLoadManager::ProcessQueuedSaveLoad heads every frame, 0x0086ff70 counts frames (10,061 menu frames in this run); the loading screen thread presents earlier, during the load | the first Present (Null/Px Present, nx::Renderer::Do_EndFrame) on the main thread after 0x00851d90 was entered, after main-menu |
| first-cell | needs input: `coc` (or New Game) runs inside a menu frame; one frame call loads the cell: 0x00852d20 BGSSaveLoadReferencesMap::LoadCellReferences (seq 616702), then in-cell frames 0x00950290 PlayerCharacter::UpdateCameraMode, 0x00778b10 HUDMainMenu::RefreshTargetDisplay | 0x00852d20 entered, then the parentCell store (`*0x011dea3c+0x40`) of a pointer to a TESObjectCELL (its vptr), then 0x00950290 in a later frame (0x0086ff70 called after the store) |

A boot with no input stops at the menu frame loop in the original too: first-cell needs the scenario's input.
tools/scenario/scenarios.json `boot-cell` (menu, then `coc GSDocMitchellHouse`) gives it, through
tools/scenario/emu_scenario.py (`EMU_PY` in tests/s5/farm_boot.sh). Threads the original starts between
masters-loaded and first-cell: one BSThread (BuildCompiledFileList, re-opens the ESM) and audio threads per music
file; none during the menu or the cell load (the cell's assets come from the open archives; IO workers already run).
