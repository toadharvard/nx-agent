#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "fnvnx_sha256.h"
static int hx(const char*s,unsigned char*o){int n=0;for(;s[0]&&s[1]&&s[0]!='\n'&&s[0]!='\r';s+=2)sscanf(s,"%2hhx",&o[n++]);return n;}
int main(int c,char**v){long ok=0,bad=0;
 for(int i=1;i<c;i++){FILE*f=fopen(v[i],"r");static char l[1<<22];long len=-1;static unsigned char m[1<<21],d[32],e[32];
  while(fgets(l,sizeof l,f)){
   if(!strncmp(l,"Len = ",6))len=atol(l+6);
   else if(!strncmp(l,"Msg = ",6)){memset(m,0,16);if(len)hx(l+6,m);}
   else if(!strncmp(l,"MD = ",5)){hx(l+5,e);fnvnx_sha256(m,(size_t)len/8,d);if(memcmp(d,e,32))bad++;else ok++;}}
  fclose(f);}
 unsigned char d[32],e[32];
 #define CHK(p,n,hex) do{fnvnx_sha256(p,n,d);hx(hex,e);if(memcmp(d,e,32))bad++;else ok++;}while(0)
 CHK("abc",3,"ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad");
 CHK("",0,"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855");
 {const char*m2="abcdbcdecdefdefgefghfghighijhijkijkljklmklmnlmnomnopnopq";CHK(m2,strlen(m2),"248d6a61d20638b8e5c026930c3e6039a33ce45964ff2167f6ecedd419db06c1");}
 {char*a=malloc(1000000);memset(a,'a',1000000);CHK(a,1000000,"cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0");free(a);}
 printf("ok %ld mismatches %ld\n",ok,bad);return bad!=0;}
