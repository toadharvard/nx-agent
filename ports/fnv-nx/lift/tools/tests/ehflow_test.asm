bits 32
org 0x1000
prolog:
 push 0x2000
 push dword [fs:0]
 mov eax,[esp+0x10]
 mov [esp+0x10], ebp
 lea ebp,[esp+0x10]
 sub esp,eax
 push ebx
 push esi
 push edi
 mov eax,[0x3000]
 xor [ebp-4], eax
 xor eax, ebp
 push eax
 mov [ebp-0x18], esp
 push dword [ebp-8]
 mov eax,[ebp-4]
 mov dword [ebp-4], 0xfffffffe
 mov [ebp-8], eax
 lea eax,[ebp-0x10]
 mov [fs:0], eax
 ret
times 0x100-($-$$) nop
epilog:
 mov ecx,[ebp-0x10]
 mov [fs:0], ecx
 pop ecx
 pop edi
 pop edi
 pop esi
 pop ebx
 mov esp,ebp
 pop ebp
 push ecx
 ret
times 0x200-($-$$) nop
g:
 ret
times 0x300-($-$$) nop
fa:                      ; positive: pair helpers around a call
 push 0x10
 push 0x4000
 call prolog
 call g
 call epilog
 ret
times 0x400-($-$$) nop
fb:                      ; negative: the address of the ebx save slot is passed to a callee
 push 0x10
 push 0x4000
 call prolog
 lea eax,[ebp-0x2c]
 push eax
 call g
 pop ecx
 call epilog
 ret
times 0x500-($-$$) nop
fc:                      ; negative: a store at a constant offset overlaps the ebx save slot
 push 0x10
 push 0x4000
 call prolog
 mov dword [ebp-0x2c], 5
 call epilog
 ret
times 0x600-($-$$) nop
fd:                      ; positive: push/pop around a call
 push ebx
 call g
 pop ebx
 ret
times 0x700-($-$$) nop
fe:                      ; negative: the slot address escapes
 push ebx
 push esp
 call g
 pop ecx
 pop ebx
 ret
times 0x800-($-$$) nop
ff:                      ; positive: the callee clobbers ebx but this function restores it from its save slot
 push ebx
 call gb
 pop ebx
 ret
 times 0x900-($-$$) nop
gb:
 xor ebx,ebx
 ret
