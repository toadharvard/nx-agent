// SPDX-License-Identifier: MIT
// mtp_fs: line protocol over stdin/stdout for DBI MTP (057e:201d).
// Request: one line, fields separated by TAB. Reply: one JSON line {"ok":...}.
//   S                              storages
//   C                              capabilities
//   L <storage> <parent>           list children (parent 4294967295 = storage root)
//   I <id>                         object metadata
//   G <id> <local>                 download to a local file
//   P <storage> <parent> <name> <local>   upload a new file, returns its id
//   W <id> <local>                 overwrite an existing file in place (edit extension)
//   R <id> <name>                  rename
//   V <id> <storage> <parent>      move
//   D <id>                         delete
//   M <storage> <parent> <name>    create folder, returns its id
#include <libmtp.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>

#define ROOT 0xffffffffu
#define CHUNK (1024 * 1024)

static LIBMTP_mtpdevice_t *device;

static void json_string(const char *s)
{
    putchar('"');

    for (; s && *s; s++) {
        unsigned char c = (unsigned char)*s;

        if (c == '"' || c == '\\')
            printf("\\%c", c);
        else if (c < 32)
            printf("\\u%04x", c);
        else
            putchar(c);
    }

    putchar('"');
}

static void fail(const char *what)
{
    printf("{\"ok\":false,\"error\":");
    json_string(what);
    printf(",\"stack\":[");

    int first = 1;

    for (LIBMTP_error_t *e = device ? LIBMTP_Get_Errorstack(device) : NULL; e; e = e->next) {
        if (!first)
            putchar(',');

        first = 0;
        json_string(e->error_text);
    }

    printf("]}\n");

    if (device)
        LIBMTP_Clear_Errorstack(device);
}

static uint32_t parent_for_write(uint32_t parent)
{
    return parent == ROOT ? 0 : parent;
}

static void print_object(const LIBMTP_file_t *f)
{
    printf("{\"id\":%u,\"parent\":%u,\"storage\":%u,\"size\":%llu,\"folder\":%s,\"name\":",
           f->item_id, f->parent_id, f->storage_id, (unsigned long long)f->filesize,
           f->filetype == LIBMTP_FILETYPE_FOLDER ? "true" : "false");
    json_string(f->filename);
    putchar('}');
}

static void cmd_storages(void)
{
    if (LIBMTP_Get_Storage(device, LIBMTP_STORAGE_SORTBY_NOTSORTED) != 0) {
        fail("get storage");

        return;
    }

    printf("{\"ok\":true,\"storages\":[");

    for (LIBMTP_devicestorage_t *s = device->storage; s; s = s->next) {
        printf("%s{\"id\":%u,\"access\":%u,\"max\":%llu,\"free\":%llu,\"description\":",
               s == device->storage ? "" : ",", s->id, s->AccessCapability,
               (unsigned long long)s->MaxCapacity, (unsigned long long)s->FreeSpaceInBytes);
        json_string(s->StorageDescription);
        putchar('}');
    }

    printf("]}\n");
}

static void cmd_capabilities(void)
{
    printf("{\"ok\":true,\"edit\":%s,\"move\":%s,\"copy\":%s}\n",
           LIBMTP_Check_Capability(device, LIBMTP_DEVICECAP_EditObjects) ? "true" : "false",
           LIBMTP_Check_Capability(device, LIBMTP_DEVICECAP_MoveObject) ? "true" : "false",
           LIBMTP_Check_Capability(device, LIBMTP_DEVICECAP_CopyObject) ? "true" : "false");
}

static void cmd_list(uint32_t storage, uint32_t parent)
{
    LIBMTP_Clear_Errorstack(device);
    LIBMTP_file_t *f = LIBMTP_Get_Files_And_Folders(device, storage, parent);

    if (!f && LIBMTP_Get_Errorstack(device)) {
        fail("list");

        return;
    }

    printf("{\"ok\":true,\"entries\":[");

    for (int first = 1; f; first = 0) {
        LIBMTP_file_t *next = f->next;

        if (!first)
            putchar(',');

        print_object(f);
        LIBMTP_destroy_file_t(f);
        f = next;
    }

    printf("]}\n");
}

static void cmd_info(uint32_t id)
{
    LIBMTP_file_t *f = LIBMTP_Get_Filemetadata(device, id);

    if (!f) {
        fail("no such object");

        return;
    }

    printf("{\"ok\":true,\"object\":");
    print_object(f);
    printf("}\n");
    LIBMTP_destroy_file_t(f);
}

static void cmd_get(uint32_t id, const char *local)
{
    if (LIBMTP_Get_File_To_File(device, id, local, NULL, NULL) != 0) {
        fail("download");

        return;
    }

    printf("{\"ok\":true}\n");
}

static void cmd_put(uint32_t storage, uint32_t parent, const char *name, const char *local)
{
    struct stat st;

    if (stat(local, &st) != 0) {
        fail("local file missing");

        return;
    }

    LIBMTP_file_t *f = LIBMTP_new_file_t();
    f->filename = strdup(name);
    f->filesize = (uint64_t)st.st_size;
    f->filetype = LIBMTP_FILETYPE_UNKNOWN;
    f->storage_id = storage;
    f->parent_id = parent_for_write(parent);

    if (LIBMTP_Send_File_From_File(device, local, f, NULL, NULL) != 0) {
        LIBMTP_destroy_file_t(f);
        fail("upload");

        return;
    }

    printf("{\"ok\":true,\"id\":%u}\n", f->item_id);
    LIBMTP_destroy_file_t(f);
}

static int write_in_place(uint32_t id, FILE *in, uint64_t size)
{
    unsigned char *buffer = malloc(CHUNK);

    if (!buffer)
        return -1;

    int rc = 0;
    uint64_t offset = 0;

    while (rc == 0 && offset < size) {
        size_t n = fread(buffer, 1, CHUNK, in);

        if (n == 0) {
            rc = -1;

            break;
        }

        rc = LIBMTP_SendPartialObject(device, id, offset, buffer, (unsigned int)n);
        offset += n;
    }

    free(buffer);

    if (rc == 0)
        rc = LIBMTP_TruncateObject(device, id, size);

    return rc;
}

static void cmd_overwrite(uint32_t id, const char *local)
{
    FILE *in = fopen(local, "rb");
    struct stat st;

    if (!in || fstat(fileno(in), &st) != 0) {
        if (in)
            fclose(in);

        fail("local file missing");

        return;
    }

    if (LIBMTP_BeginEditObject(device, id) != 0) {
        fclose(in);
        fail("begin edit");

        return;
    }

    int rc = write_in_place(id, in, (uint64_t)st.st_size);
    int end = LIBMTP_EndEditObject(device, id);
    fclose(in);

    if (rc != 0 || end != 0) {
        fail("overwrite");

        return;
    }

    printf("{\"ok\":true}\n");
}

static void cmd_rename(uint32_t id, const char *name)
{
    if (LIBMTP_Set_Object_String(device, id, LIBMTP_PROPERTY_ObjectFileName, name) != 0) {
        fail("rename");

        return;
    }

    printf("{\"ok\":true}\n");
}

static void cmd_move(uint32_t id, uint32_t storage, uint32_t parent)
{
    if (LIBMTP_Move_Object(device, id, storage, parent_for_write(parent)) != 0) {
        fail("move");

        return;
    }

    printf("{\"ok\":true}\n");
}

static void cmd_delete(uint32_t id)
{
    if (LIBMTP_Delete_Object(device, id) != 0) {
        fail("delete");

        return;
    }

    printf("{\"ok\":true}\n");
}

static void cmd_mkdir(uint32_t storage, uint32_t parent, const char *name)
{
    char *copy = strdup(name);
    uint32_t id = LIBMTP_Create_Folder(device, copy, parent_for_write(parent), storage);
    free(copy);

    if (id == 0) {
        fail("create folder");

        return;
    }

    printf("{\"ok\":true,\"id\":%u}\n", id);
}

static uint32_t number(const char *s)
{
    return s ? (uint32_t)strtoul(s, NULL, 0) : 0;
}

static void dispatch(char **f, int n)
{
    const char *cmd = f[0];

    if (!strcmp(cmd, "S") && n == 1)
        cmd_storages();
    else if (!strcmp(cmd, "C") && n == 1)
        cmd_capabilities();
    else if (!strcmp(cmd, "L") && n == 3)
        cmd_list(number(f[1]), number(f[2]));
    else if (!strcmp(cmd, "I") && n == 2)
        cmd_info(number(f[1]));
    else if (!strcmp(cmd, "G") && n == 3)
        cmd_get(number(f[1]), f[2]);
    else if (!strcmp(cmd, "P") && n == 5)
        cmd_put(number(f[1]), number(f[2]), f[3], f[4]);
    else if (!strcmp(cmd, "W") && n == 3)
        cmd_overwrite(number(f[1]), f[2]);
    else if (!strcmp(cmd, "R") && n == 3)
        cmd_rename(number(f[1]), f[2]);
    else if (!strcmp(cmd, "V") && n == 4)
        cmd_move(number(f[1]), number(f[2]), number(f[3]));
    else if (!strcmp(cmd, "D") && n == 2)
        cmd_delete(number(f[1]));
    else if (!strcmp(cmd, "M") && n == 4)
        cmd_mkdir(number(f[1]), number(f[2]), f[3]);
    else
        fail("bad request");
}

static int open_device(void)
{
    LIBMTP_raw_device_t *raw = NULL;
    int count = 0;

    LIBMTP_Init();

    if (LIBMTP_Detect_Raw_Devices(&raw, &count) != LIBMTP_ERROR_NONE)
        count = 0;

    for (int i = 0; i < count && !device; i++) {
        if (raw[i].device_entry.vendor_id == 0x057e && raw[i].device_entry.product_id == 0x201d)
            device = LIBMTP_Open_Raw_Device_Uncached(&raw[i]);
    }

    free(raw);

    return device != NULL;
}

int main(void)
{
    setvbuf(stdout, NULL, _IOLBF, 0);

    if (!open_device()) {
        printf("{\"ok\":false,\"error\":\"DBI MTP device 057e:201d not found or not claimable\",\"open\":false}\n");

        return 3;
    }

    printf("{\"ok\":true,\"ready\":true}\n");

    char *line = NULL;
    size_t cap = 0;
    ssize_t len;

    while ((len = getline(&line, &cap, stdin)) > 0) {
        while (len > 0 && (line[len - 1] == '\n' || line[len - 1] == '\r'))
            line[--len] = 0;

        char *fields[6];
        int n = 0;

        for (char *p = line, *tab; n < 6; p = tab + 1) {
            fields[n++] = p;
            tab = strchr(p, '\t');

            if (!tab)
                break;

            *tab = 0;
        }

        if (n == 0 || !fields[0][0])
            break;

        dispatch(fields, n);
    }

    free(line);
    LIBMTP_Release_Device(device);

    return 0;
}
