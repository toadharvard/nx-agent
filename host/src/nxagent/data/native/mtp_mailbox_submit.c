// SPDX-License-Identifier: MIT
#include <libmtp.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
static uint32_t child(LIBMTP_mtpdevice_t*d,uint32_t parent,const char*name){uint32_t id=0;LIBMTP_file_t*f=LIBMTP_Get_Files_And_Folders(d,0x10001,parent);while(f){LIBMTP_file_t*n=f->next;if(!strcmp(f->filename,name)){if(id){fprintf(stderr,"Duplicate path\n");exit(3);}id=f->item_id;}LIBMTP_destroy_file_t(f);f=n;}if(!id){fprintf(stderr,"Path not found: %s\n",name);exit(3);}return id;}
int main(int argc,char**argv){
 if(argc!=2)return 2;
 unsigned char bytes[129];FILE*f=fopen(argv[1],"rb");if(!f)return 2;size_t size=fread(bytes,1,sizeof(bytes),f);fclose(f);if(size!=128||memcmp(bytes,"SCMAIL01",8))return 2;
 LIBMTP_Init();LIBMTP_raw_device_t*r=NULL;int n=0;if(LIBMTP_Detect_Raw_Devices(&r,&n)!=LIBMTP_ERROR_NONE||n!=1)return 3;
 if(r[0].device_entry.vendor_id!=0x057e||r[0].device_entry.product_id!=0x201d)return 3;
 LIBMTP_mtpdevice_t*d=LIBMTP_Open_Raw_Device_Uncached(&r[0]);free(r);if(!d)return 3;
 uint32_t parent=child(d,child(d,0xffffffff,"config"),"usb-worker-mailbox"),id=0;
 LIBMTP_file_t*entry=LIBMTP_Get_Files_And_Folders(d,0x10001,parent);
 while(entry){LIBMTP_file_t*next=entry->next;if(!strcmp(entry->filename,"request.bin")){if(id)return 4;id=entry->item_id;}LIBMTP_destroy_file_t(entry);entry=next;}
 int result;
 if(id){if(LIBMTP_BeginEditObject(d,id))return 5;result=LIBMTP_SendPartialObject(d,id,0,bytes,128);int end=LIBMTP_EndEditObject(d,id);if(end)result=end;}
 else{entry=LIBMTP_new_file_t();entry->filename=strdup("request.bin");entry->filesize=128;entry->filetype=LIBMTP_FILETYPE_UNKNOWN;entry->storage_id=0x10001;entry->parent_id=parent;result=LIBMTP_Send_File_From_File(d,argv[1],entry,NULL,NULL);LIBMTP_destroy_file_t(entry);}
 LIBMTP_Release_Device(d);
 if(result){fprintf(stderr,"Uncertain transfer: inspect status; do not retry command.\n");return 6;}
 puts("Submitted once; execution must be checked using sequence/status.");return 0;
}
