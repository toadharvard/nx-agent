# fnv-nx: first run on the console (owner checklist)

One page. Do the steps in order; the first two blocks are once. Takes about 15 minutes plus the copy.

## You need

- A Switch running Atmosphère (its loader applies the game's process; nothing else needs changing), system 5.0.0+.
- An SD card with about 12 GB free (exFAT or FAT32), and a computer to copy to it.
- Your own copy of the game: `FalloutNV.exe` (GOG 1.4.0.525) and its `Data` folder.
- From the private repository, branch `nro/<commit>` (the newest one): the folder `sd/switch/fnv/`.

## Copy (once)

1. Copy `sd/switch/fnv/` to the SD card as `/switch/fnv/` (it holds `fnv.nro`, `fnv-exefs.nsp`, `guest.nso`).
2. Copy your `FalloutNV.exe` into `/switch/fnv/`.
3. Copy your whole `Data` folder into `/switch/fnv/`, so you have `/switch/fnv/Data/FalloutNV.esm` and the `.bsa` files.
   Build the game's shaders for the console once, from your `Data/Shaders` on the computer (Linux or an agent
   container; nx-agent checkout, `ports/fnv-nx`): `python3 toolchain/nro/shaders_nx.py <Data/Shaders> -o
   shaders_nx.pak`, then copy `shaders_nx.pak` into `/switch/fnv/`. It is made from your game files: keep it to
   yourself. Without it, `host.log` says `game shaders: ... missing`.

## Start

4. On the HOME menu, **hold R** and start any installed game (a cartridge or a download); keep holding R until
   hbmenu appears. Starting hbmenu from the Album does not work: it gets too little memory, and fnv-nx says so.
5. In hbmenu pick **fnv-nx**. It lists what it found. Every line should say `ok`.
6. Press **A**, then let go of every button. The console restarts that game with fnv-nx's process (your game is not
   changed: fnv-nx removes its override as soon as it runs).

## What you should see

- The launcher's text screen, then a black screen: the game process has no text console (the native renderer owns
  the display). At the current stage the game stops early and draws nothing yet; that is expected.
- Wait about 30 seconds, then leave (below). `host.log` shows how far it got (`fnvnx host:` and `fnvnx boot:` lines).
- To leave: HOME, then close the software. You come back to the HOME menu (not hbmenu: hbmenu lived in the
  process the game replaced).

## If something goes wrong

| what you see | what to do |
|---|---|
| fnv-nx says `applet mode` | you started hbmenu from the Album: use step 4 (hold R on a game) |
| a line says `MISSING` | copy that file again; names must be as in steps 1-3 |
| `already exists and is not fnv-nx's` | that game has a mod of its own: use another game in step 4 |
| back to HOME right after A, or an error code | write down the code; send `launcher.log` |
| a system error dialog with the game's own text (its MessageBox) | take a photo, press OK; it may continue |
| a crash screen, or HOME does nothing | hold POWER 12 s, start again; send `crash.log` and `host.log` |
| the game you used in step 4 starts fnv-nx by itself afterwards | start fnv-nx (steps 4-5) and press **Y** (removes the leftover) |

## Send back

From `/switch/fnv/` on the SD card: **`launcher.log`**, **`host.log`**, **`crash.log`** (if there is one), and a phone
photo of the screen where it stopped. Also say: docked or handheld, system version, Atmosphère version, which game
you used in step 4.
