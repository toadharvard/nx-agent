/* fnv-nx P6n: entry of the deko3d counting harness (Unicorn sets sp; no OS) */
    .text
    .global _start
_start:
    bl main
1:  b 1b
