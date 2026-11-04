/* port_defaults.cpp - V1 (D14) against S15-threads 4364484 (platform/win32/ini.cpp port_default): Win32
 * GetPrivateProfile* semantics must hold for the keys the Switch table supplies. Built with tests/s4's harness
 * (main.cpp, s4_test.h) by tests/v1/test_ini_port_defaults.py. Synthetic ini text only. */
#include "../../s4/s4_test.h"

static const char INI[] = "C:\\Users\\Player\\Documents\\My Games\\FalloutNV\\Fallout.ini";
static const char DEF[] = "C:\\FalloutNV\\FALLOUT_DEFAULT.INI";

/* I1: a key the file has wins, whatever its value or spelling; absent keys only get the table's value */
TEST(v1_ini_file_wins)
{
    s4_write_file(INI, "[general]\r\n"
                       "BUSETHREADEDMORPHER = 0 \r\n"            /* other case, blanks */
                       "bUseThreadedParticleSystem=\r\n"         /* present, empty: the caller's default, not 1 */
                       ";bUseThreadedTempEffects=0\r\n"          /* commented out: absent */
                       "[General]\r\n"
                       "bUseThreadedTempEffects=0\r\n");         /* second section of the name: not searched */
    char b[64];
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedMorpher", 7, INI), 0);
    CHECK_EQ(GetPrivateProfileStringA("General", "bUseThreadedParticleSystem", "dflt", b, sizeof b, INI), 0);
    CHECK_STR(b, "");
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedParticleSystem", 7, INI), 7);
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedTempEffects", 0, INI), 1);
}

/* I2: return value and truncation of a table value are GetPrivateProfileString's: n - 1 chars, NUL, count */
TEST(v1_ini_truncation)
{
    s4_write_file(INI, "[Display]\r\niSize W=1280\r\n");
    char b[4] = {'x', 'x', 'x', 'x'};
    CHECK_EQ(GetPrivateProfileStringA("General", "bUseThreadedMorpher", "", b, 1, INI), 0);
    CHECK_EQ(b[0], 0);
    CHECK_EQ(b[1], 'x');
    CHECK_EQ(GetPrivateProfileStringA("General", "bUseThreadedMorpher", "", b, 2, INI), 1);
    CHECK_STR(b, "1");
    CHECK_EQ(GetPrivateProfileStringA("General", "bUseThreadedMorpher", "", b, 0, INI), 0);
    /* a missing [General] section: the table still answers, error cleared */
    CHECK_EQ(GetPrivateProfileIntA("general", "busethreadedmorpher", 0, INI), 1);
}

/* I3: the switch: [fnvnx] in any case, bPortDefaults=0 / 0x0 off; only for its own file */
TEST(v1_ini_switch)
{
    s4_write_file(INI, "[FNVNX]\r\nbportdefaults = 0x0\r\n");
    s4_write_file(DEF, "[General]\r\n");
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedMorpher", 7, INI), 7);
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedMorpher", 7, DEF), 1);   /* upper-case file name */
    /* other files keep the caller's default; a name that only ends like the engine's is not it */
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedMorpher", 7, "C:\\FalloutNV\\MyFallout.ini"), 7);
    CHECK_EQ(GetPrivateProfileIntA("General", "bUseThreadedMorpher", 7, "C:\\FalloutNV\\Fallout.ini.bak"), 7);
    /* listings stay the file's */
    char l[64];
    memset(l, 'x', sizeof l);
    CHECK_EQ(GetPrivateProfileStringA("General", NULL, "", l, sizeof l, DEF), 0);
    CHECK_EQ(l[0], 0);
    CHECK_EQ(GetPrivateProfileSectionA("General", l, sizeof l, DEF), 0);
}
