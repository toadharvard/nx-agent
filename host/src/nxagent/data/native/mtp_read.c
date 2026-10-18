// SPDX-License-Identifier: MIT
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
static void js(const char*s){putchar('"');for(;s&&*s;s++){unsigned char c=*s;if(c=='"'||c=='\\')printf("\\%c",c);else if(c<32)printf("\\u%04x",c);else putchar(c);}putchar('"');}
int main(){LIBMTP_Init();LIBMTP_raw_device_t*r=NULL;int n=0;if(LIBMTP_Detect_Raw_Devices(&r,&n)!=LIBMTP_ERROR_NONE||n!=1){fprintf(stderr,"Expected one MTP device, got %d\n",n);return 2;}LIBMTP_mtpdevice_t*d=LIBMTP_Open_Raw_Device_Uncached(&r[0]);free(r);if(!d)return 3;char*line=NULL;size_t cap=0;while(getline(&line,&cap,stdin)>0){char*cmd=strtok(line,"\t\r\n");char*ids=strtok(NULL,"\t\r\n");if(!cmd||!ids)break;uint32_t id=(uint32_t)strtoul(ids,NULL,0);if(!strcmp(cmd,"L")){LIBMTP_file_t*f=LIBMTP_Get_Files_And_Folders(d,0x10001,id);printf("@JSON [");int first=1;while(f){LIBMTP_file_t*next=f->next;if(!first)putchar(',');first=0;printf("{\"id\":%u,\"parent\":%u,\"size\":%llu,\"folder\":%s,\"name\":",f->item_id,f->parent_id,(unsigned long long)f->filesize,f->filetype==LIBMTP_FILETYPE_FOLDER?"true":"false");js(f->filename);putchar('}');LIBMTP_destroy_file_t(f);f=next;}puts("]");}else if(!strcmp(cmd,"G")){char*path=strtok(NULL,"\r\n");if(!path)break;int e=LIBMTP_Get_File_To_File(d,id,path,NULL,NULL);printf("@JSON {\"result\":%d}\n",e);if(e)LIBMTP_Dump_Errorstack(d);}else break;fflush(stdout);}free(line);LIBMTP_Release_Device(d);return 0;}
