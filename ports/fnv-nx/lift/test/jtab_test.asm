bits 32
org 0x1000
swa:  cmp eax, 3
      ja dflt
      jmp [eax*4+0x1400]
align 0x20, nop
swb:  cmp eax, 4
      jae dflt
      jmp [eax*4+0x1400]
align 0x20, nop
swc:  mov eax, [esp+4]
      jmp [eax*4+0x1400]
align 0x20, nop
swd:  cmp eax, 3
      ja dflt
mid:  jmp [eax*4+0x1400]
align 0x20, nop
ext:  jmp mid
align 0x20, nop
swe:  cmp eax, 5
      ja dflt
      movzx ecx, byte [eax+0x1500]
      jmp [ecx*4+0x1400]
align 0x20, nop
swf:  cmp eax, 4
      ja dflt
      jmp [eax*4+0x1400]
align 0x20, nop
swg:  and eax, 3
      jmp [eax*4+0x1400]
align 0x20, nop
swh:  cmp eax, 3
      jbe dflt
      jmp [eax*4+0x1400]
align 0x20, nop
dflt: ret
times 0x400-($-$$) db 0x90
dd c0, c1, c2, c3, 0x12345678
times 0x500-($-$$) db 0x90
db 0,1,2,3,3,0
times 0x600-($-$$) db 0x90
c0: ret
c1: ret
c2: ret
c3: ret
