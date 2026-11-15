# fpmask WIP (w11, stopped by the L3a lead: off the path until after the first frame)
Done: tools/fpmask.py + ctlloads.py + root lift/fpmask (recipe check passes); fpmask.tsv on fnv-nx-build l3a-lift/fpmask. 1111 sites, 935 proved, 176 undecided (ptr 82, alias 47, uninit 12, call 13, arg 5, env-load 4, espebp 3, global 10); no site clears a mask bit.
Not done: interprocedural args, global-slot writers, object-field control words, frstor/fldenv/fxrstor backward trace, alias refinement; recipe build not run (pinned env missing here).
