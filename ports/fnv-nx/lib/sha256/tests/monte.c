#include <stdio.h>
#include <string.h>
#include "fnvnx_sha256.h"
static int hx(const char*s,unsigned char*o){int n=0;for(;s[0]&&s[1]&&s[0]!='\n'&&s[0]!='\r';s+=2)sscanf(s,"%2hhx",&o[n++]);return n;}
int main(int c,char**v){FILE*f=fopen(v[1],"r");char l[512];unsigned char seed[32],e[32],A[32],B[32],C[32],M[96],MD[32];long ok=0,bad=0;
 while(fgets(l,sizeof l,f)){
  if(!strncmp(l,"Seed = ",7)){hx(l+7,seed);memcpy(MD,seed,32);}
  else if(!strncmp(l,"MD = ",5)){hx(l+5,e);
   memcpy(A,MD,32);memcpy(B,MD,32);memcpy(C,MD,32);
   for(int i=3;i<1003;i++){memcpy(M,A,32);memcpy(M+32,B,32);memcpy(M+64,C,32);fnvnx_sha256(M,96,MD);memcpy(A,B,32);memcpy(B,C,32);memcpy(C,MD,32);}
   if(memcmp(MD,e,32))bad++;else ok++;}}
 printf("monte ok %ld mismatches %ld\n",ok,bad);return bad!=0;}
