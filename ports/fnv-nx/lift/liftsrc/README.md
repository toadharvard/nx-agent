lift/liftsrc: the lifted C++ source of the pack as recipe stages (shard stages + one finish stage). Decisions:
- Fixed stage set: the number N of shards of an image is chosen from the image size alone and declared in build.json: the exe 64, an image with size_of_image >= 2 MiB (d3dx9_31, d3dx9_38) 16,
  every other row of the loader's table 1 (tools/liftsrcgen.py shards()). Shard K takes the functions whose entry has bucket(entry) % N == K, bucket = (entry >> 4) & 0xfff, the rule of the pack's units.
  A facts change re-keys stages and never changes the stage set; nobody regenerates build.json after a facts change.
- finish checks that the union of the shards of every image equals its UNIVERSE (a function no shard took, or one taken twice, is red) and that the images are the exe plus every row of the loader's table.
- Inputs: UNIVERSE, jump-table facts and per-module facts from lift/facts FACTS; FPSTATE and FPDEPTH from lift/fpfacts; module table from the loader; X86SYS from the platform; all through `uses`.
