#define FNVNX_CORE3_DEFAULT 0
static const fnvnx_role_t roles[] = {
    [FNVNX_ROLE_MAIN]   = {"main",    0, 0x1, 0, 0},
    [FNVNX_ROLE_GAME]   = {"game",   -1, 0x6, 0xE, 0},
    [FNVNX_ROLE_RENDER] = {"render",  1, 0x2, 0, 0x2B},
};
