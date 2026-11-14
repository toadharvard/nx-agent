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
align 0x40, nop
m1:   mov edx, ecx
      and edx, 3
      shr ecx, 2
      cmp ecx, 8
      jb dflt
      rep movsd
      jmp [edx*4+0x1700]
align 0x40, nop
m2:   sub ecx, 4
      jb m2w
      ret
m2w:  jmp [ecx*4+0x1720]
align 0x40, nop
m3:   cmp ecx, 8
      jb m3n
      ret
m3n:  neg ecx
      jmp [ecx*4+0x174c]
align 0x40, nop
m4:   sub ecx, 4
      jb m4w
      ret
m4w:  jmp [ecx*4+0x1770]
align 0x40, nop
m5:   mov edx, 3
      sub ecx, 4
      jb dflt
      and edx, ecx
      jmp [edx*4+0x1700]
align 0x40, nop
m6:   cmp ecx, 1
      jne m6b
      mov eax, 1
      jmp m6j
m6b:  mov eax, 2
m6j:  jmp [eax*4+0x1700]
align 0x40, nop
m7:   xor ecx, ecx
m7l:  inc ecx
      cmp ecx, 100
      jb m7l
      jmp [ecx*4+0x1700]
times 0x400-($-$$) db 0x90
dd c0, c1, c2, c3, 0x12345678
times 0x500-($-$$) db 0x90
db 0,1,2,3,3,0
times 0x600-($-$$) db 0x90
c0: ret
c1: ret
c2: ret
c3: ret
times 0x700-($-$$) db 0x90
dd c0, c1, c2, c3
times 0x710-($-$$) db 0x90
dd c0, c1, c2, c3
times 0x730-($-$$) db 0x90
dd c0, c1, c2, c3, c0, c1, c2, c3
times 0x760-($-$$) db 0x90
dd c0, c1, 0x77777777, c3
