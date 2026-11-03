/* test_iob.cpp - the game's FILE (struct _iobuf) and MSVC text mode: platform/crt/iob.c (S4b). */
#include "s4_test.h"

#include <errno.h>
#include <stdlib.h>

#include <string>

#include "fnvnx_iobuf.h"
#include "fnvnx_msvcrt.h"

namespace {
std::string slurp(const char *win)
{
    std::string r;
    HANDLE h = CreateFileA(win, GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (h == INVALID_HANDLE_VALUE)
        return "<missing>";
    char b[512];
    DWORD got;
    while (ReadFile(h, b, sizeof b, &got, NULL) && got)
        r.append(b, got);
    CloseHandle(h);
    return r;
}

void put_file(const char *win, const std::string &data)
{
    HANDLE h = CreateFileA(win, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
    DWORD put = 0;
    WriteFile(h, data.data(), (DWORD)data.size(), &put, NULL);
    CloseHandle(h);
}
} // namespace

TEST(iob_layout_and_std_streams)
{
    _iobuf *iob = __iob_func();
    CHECK(iob == fnvnx_iob);
    CHECK_EQ((char *)&iob[1] - (char *)&iob[0], (long long)sizeof(_iobuf));
    CHECK_EQ(sizeof(_iobuf), 4 * sizeof(void *) + 4 * sizeof(int)); /* 32 bytes on ILP32 (0x20 stride) */
    CHECK_EQ(offsetof(_iobuf, _flag), 3 * sizeof(void *));
    CHECK_EQ(offsetof(_iobuf, _file), 3 * sizeof(void *) + sizeof(int));
    for (int i = 0; i < 3; i++) {
        CHECK_EQ(iob[i]._file, i);
        CHECK_EQ(fnvnx_iob_fileno(&iob[i]), i);
        CHECK(iob[i]._ptr == NULL && iob[i]._cnt == 0 && iob[i]._base == NULL);
    }
    CHECK(iob[0]._flag & FNVNX_IOREAD);
    CHECK(iob[1]._flag & FNVNX_IOWRT);
    CHECK_EQ(fnvnx_iob_fgetc(&iob[0]), EOF); /* no console input */
    CHECK(fnvnx_iob_feof(&iob[0]));
    fnvnx_iob_clearerr(&iob[0]);
    CHECK(!fnvnx_iob_feof(&iob[0]));
    CHECK_EQ(fnvnx_iob_fprintf(&iob[1], "%s", ""), 0);
    CHECK_EQ(fnvnx_iob_fflush(&iob[1]), 0);
    CHECK_EQ(fnvnx_iob_fflush(NULL), 0);
    CHECK_EQ(fnvnx_iob_fseek(&iob[1], 0, SEEK_SET), -1); /* a device */
    CHECK_EQ(fnvnx_setmode(1, _O_BINARY), _O_TEXT);    /* the standard streams start in text mode */
    CHECK_EQ(fnvnx_setmode(1, _O_TEXT), _O_BINARY);
    /* not an fnvnx stream: rejected, no crash */
    CHECK_EQ(fnvnx_iob_fputc('x', (_iobuf *)stderr), EOF);
    CHECK_EQ(errno, EINVAL);
}

TEST(iob_text_read)
{
    CreateDirectoryA("Data", NULL);
    put_file("Data\\text.txt", std::string("a\r\nb\rc\r\n\r\nd\x1atail"));
    _iobuf *f = fnvnx_iob_fopen("Data\\TEXT.txt", "r");
    CHECK(f != NULL);
    if (!f)
        return;
    CHECK(f->_ptr == NULL && f->_cnt == 0 && f->_base == NULL); /* MSVC filebuf code must call fgetc */
    char b[64];
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "a\n");
    CHECK_EQ(fnvnx_iob_ftell(f), 3); /* raw offset */
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "b\rc\n"); /* a lone CR stays */
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "\n");
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "d");      /* Ctrl-Z ends the file */
    CHECK(fnvnx_iob_feof(f));
    CHECK(fnvnx_iob_fgets(b, sizeof b, f) == NULL);
    CHECK_EQ(fnvnx_iob_ftell(f), 11);                      /* at the Ctrl-Z */
    CHECK_EQ(fnvnx_iob_fgetc(f), EOF);
    fnvnx_iob_clearerr(f);                                 /* clears the descriptor's ^Z state too: hits it again */
    CHECK_EQ(fnvnx_iob_fgetc(f), EOF);
    fnvnx_iob_rewind(f);
    CHECK(!fnvnx_iob_feof(f));
    CHECK_EQ(fnvnx_iob_fread(b, 1, sizeof b, f), 8u);
    CHECK(!memcmp(b, "a\nb\rc\n\nd", 8));
    CHECK_EQ(fnvnx_iob_fclose(f), 0);

    /* binary: bytes as they are, Ctrl-Z included */
    f = fnvnx_iob_fopen("Data\\text.txt", "rb");
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "a\r\n");
    CHECK_EQ(fnvnx_iob_fread(b, 1, sizeof b, f), 13u);
    CHECK(!memcmp(b, "b\rc\r\n\r\nd\x1atail", 13));
    fnvnx_iob_fclose(f);

    /* _fmode decides when the mode has neither t nor b */
    CHECK_EQ(fnvnx_set_fmode(_O_BINARY), 0);
    f = fnvnx_iob_fopen("Data\\text.txt", "r");
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "a\r\n");
    fnvnx_iob_fclose(f);
    int m = 0;
    CHECK_EQ(fnvnx_get_fmode(&m), 0);
    CHECK_EQ(m, _O_BINARY);
    fnvnx_set_fmode(_O_TEXT);
    f = fnvnx_iob_fopen("Data\\text.txt", "rt");
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "a\n");
    fnvnx_iob_fclose(f);
}

TEST(iob_text_read_buffer_edges)
{
    /* CR LF and a lone CR exactly on the 4096-byte read-buffer boundary */
    std::string s(4095, 'x');
    s += "\r\ny";
    s += std::string(4093, 'z'); /* lone CR at offset 8191, the last byte of the second buffer */
    s += "\rq\r";
    put_file("Data\\edge.txt", s);
    _iobuf *f = fnvnx_iob_fopen("Data\\edge.txt", "r");
    std::string got;
    int c;
    while ((c = fnvnx_iob_fgetc(f)) != EOF)
        got += (char)c;
    std::string want(4095, 'x');
    want += "\ny";
    want += std::string(4093, 'z');
    want += "\rq\r"; /* CR before 'q' and at end of file stay */
    CHECK_EQ(got.size(), want.size());
    CHECK(got == want);
    CHECK_EQ(fnvnx_iob_ftelli64(f), (long long)s.size());
    /* the same through fread in one go */
    fnvnx_iob_rewind(f);
    std::string all(s.size(), 0);
    size_t n = fnvnx_iob_fread(&all[0], 1, all.size(), f);
    all.resize(n);
    CHECK(all == want);
    /* ftell after a CR LF across the boundary is the raw offset */
    fnvnx_iob_fseek(f, 4090, SEEK_SET);
    char b[16];
    CHECK_EQ(fnvnx_iob_fread(b, 1, 7, f), 7u);
    CHECK(!memcmp(b, "xxxxx\ny", 7));
    CHECK_EQ(fnvnx_iob_ftell(f), 4098);
    fnvnx_iob_fclose(f);
}

TEST(iob_text_write)
{
    _iobuf *f = fnvnx_iob_fopen("Data\\out.txt", "w");
    CHECK(f != NULL);
    if (!f)
        return;
    CHECK_EQ(fnvnx_iob_fputs("l1\nl2\n", f), 0);
    CHECK_EQ(fnvnx_iob_ftell(f), 8); /* CR counted: raw offset */
    CHECK_EQ(fnvnx_iob_fprintf(f, "%d %I64d\n", 5, 1LL << 40), 16);
    CHECK_EQ(fnvnx_iob_fputc('\n', f), '\n');
    CHECK_EQ(fnvnx_iob_fwrite("a\nb", 1, 3, f), 3u);
    CHECK_EQ(fnvnx_iob_fwrite("12345678", 4, 2, f), 2u);
    CHECK_EQ(fnvnx_iob_fclose(f), 0);
    CHECK(slurp("Data\\out.txt") == "l1\r\nl2\r\n5 1099511627776\r\n\r\na\r\nb12345678");

    f = fnvnx_iob_fopen("Data\\out.txt", "wb");
    fnvnx_iob_fputs("l1\nl2\n", f);
    fnvnx_iob_fclose(f);
    CHECK(slurp("Data\\out.txt") == "l1\nl2\n");

    /* large text write (buffer flushes) and the read-back */
    f = fnvnx_iob_fopen("Data\\big.txt", "wt");
    std::string line(99, 'k');
    line += '\n';
    for (int i = 0; i < 200; i++)
        fnvnx_iob_fwrite(line.data(), 1, line.size(), f);
    CHECK_EQ(fnvnx_iob_ftell(f), 200 * 101);
    fnvnx_iob_fclose(f);
    CHECK_EQ(slurp("Data\\big.txt").size(), 200u * 101u);
    f = fnvnx_iob_fopen("Data\\big.txt", "r");
    std::string back(20000, 0);
    CHECK_EQ(fnvnx_iob_fread(&back[0], 1, back.size(), f), 20000u);
    CHECK(back.substr(0, 100) == line && back.substr(19900) == line);
    fnvnx_iob_fclose(f);
}

TEST(iob_seek_tell_unget)
{
    put_file("Data\\seek.txt", std::string("one\r\ntwo\r\nthree\r\n"));
    _iobuf *f = fnvnx_iob_fopen("Data\\seek.txt", "r");
    char b[32];
    fnvnx_iob_fgets(b, sizeof b, f);
    long pos = fnvnx_iob_ftell(f);
    CHECK_EQ(pos, 5);
    long long fp = -1;
    CHECK_EQ(fnvnx_iob_fgetpos(f, &fp), 0);
    CHECK_EQ(fp, 5);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "two\n");
    CHECK_EQ(fnvnx_iob_fseek(f, pos, SEEK_SET), 0);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "two\n");
    CHECK_EQ(fnvnx_iob_fsetpos(f, &fp), 0);
    CHECK_EQ(fnvnx_iob_fgetc(f), 't');
    CHECK_EQ(fnvnx_iob_ungetc('T', f), 'T');
    CHECK_EQ(fnvnx_iob_ftell(f), 5);
    CHECK_EQ(fnvnx_iob_fgetc(f), 'T');
    CHECK_EQ(fnvnx_iob_fgetc(f), 'w');
    CHECK_EQ(fnvnx_iob_fseek(f, -2, SEEK_CUR), 0); /* back to 'T's offset 5 */
    CHECK_EQ(fnvnx_iob_fgetc(f), 't');
    CHECK_EQ(fnvnx_iob_fseek(f, -7, SEEK_END), 0);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "three\n");
    CHECK_EQ(fnvnx_iob_fgetc(f), EOF);
    CHECK(fnvnx_iob_feof(f));
    CHECK_EQ(fnvnx_iob_ungetc(EOF, f), EOF);
    CHECK_EQ(fnvnx_iob_fseek(f, 0, SEEK_END), 0); /* a seek clears end of file */
    CHECK(!fnvnx_iob_feof(f));
    CHECK_EQ(fnvnx_iob_ftelli64(f), 17);
    CHECK_EQ(fnvnx_iob_fseek(f, -1, SEEK_SET), -1);
    CHECK_EQ(errno, EINVAL);
    CHECK_EQ(fnvnx_iob_fseek(f, 0, 7), -1);
    fnvnx_iob_fclose(f);
}

TEST(iob_update_and_append)
{
    put_file("Data\\rw.txt", std::string("abcdef"));
    _iobuf *f = fnvnx_iob_fopen("Data\\rw.txt", "r+b");
    CHECK(f && (f->_flag & FNVNX_IORW));
    CHECK_EQ(fnvnx_iob_fgetc(f), 'a');
    CHECK_EQ(fnvnx_iob_fgetc(f), 'b');
    CHECK_EQ(fnvnx_iob_fputc('X', f), 'X'); /* read -> write without a seek: lands at offset 2 */
    CHECK_EQ(fnvnx_iob_fgetc(f), 'd');      /* write -> read: flushed first */
    fnvnx_iob_fclose(f);
    CHECK(slurp("Data\\rw.txt") == "abXdef");

    f = fnvnx_iob_fopen("Data\\rw.txt", "a");
    fnvnx_iob_fseek(f, 0, SEEK_SET);
    fnvnx_iob_fputs("1\n", f); /* append: at the end whatever the position */
    CHECK_EQ(fnvnx_iob_ftell(f), 9);
    fnvnx_iob_fclose(f);
    CHECK(slurp("Data\\rw.txt") == "abXdef1\r\n");

    f = fnvnx_iob_fopen("Data\\rw.txt", "a+");
    char b[16];
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "abXdef1\n"); /* a+ reads from the start */
    fnvnx_iob_fseek(f, 0, SEEK_SET);
    fnvnx_iob_fputc('Z', f);
    fnvnx_iob_fclose(f);
    CHECK(slurp("Data\\rw.txt") == "abXdef1\r\nZ");

    f = fnvnx_iob_fopen("Data\\rw.txt", "w+");
    fnvnx_iob_fputs("x\ny\n", f);
    fnvnx_iob_rewind(f);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "x\n");
    fnvnx_iob_fclose(f);
}

TEST(iob_setmode_and_errors)
{
    put_file("Data\\mode.txt", std::string("p\r\nq\r\n"));
    _iobuf *f = fnvnx_iob_fopen("Data\\mode.txt", "rb");
    char b[16];
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "p\r\n");
    CHECK_EQ(fnvnx_setmode(fnvnx_iob_fileno(f), _O_TEXT), _O_BINARY); /* applies to the stream at once */
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "q\n");
    CHECK_EQ(fnvnx_setmode(fnvnx_iob_fileno(f), 0x1234), -1);
    CHECK_EQ(errno, EINVAL);
    /* reading a write-only stream / writing a read-only one */
    CHECK_EQ(fnvnx_iob_fputc('x', f), EOF);
    CHECK(fnvnx_iob_ferror(f));
    fnvnx_iob_clearerr(f);
    CHECK(!fnvnx_iob_ferror(f));
    fnvnx_iob_fclose(f);
    f = fnvnx_iob_fopen("Data\\mode2.txt", "w");
    CHECK_EQ(fnvnx_iob_fgetc(f), EOF);
    CHECK(fnvnx_iob_ferror(f));
    CHECK_EQ(fnvnx_iob_setvbuf(f, NULL, 0, 4096), 0);
    CHECK_EQ(fnvnx_iob_setvbuf(f, NULL, 3, 4096), -1);
    fnvnx_iob_fclose(f);

    CHECK(fnvnx_iob_fopen("Data\\none.txt", "r") == NULL);
    CHECK_EQ(errno, ENOENT);
    CHECK(fnvnx_iob_fopen("Data\\mode.txt", "x") == NULL);
    CHECK_EQ(errno, EINVAL);
    CHECK(fnvnx_iob_fopen("Data\\mode.txt", "rtb") == NULL);
    CHECK(fnvnx_iob_fopen("Data\\mode.txt", "r++") == NULL);
    CHECK(fnvnx_iob_fopen("Data", "r") == NULL); /* a directory */
    _iobuf *g = (_iobuf *)1;
    CHECK_EQ(fnvnx_iob_fopen_s(&g, "Data\\none.txt", "r"), ENOENT);
    CHECK(g == NULL);
    CHECK_EQ(fnvnx_iob_fopen_s(&g, "Data\\mode.txt", "rbc"), 0); /* commit flag accepted */
    fnvnx_iob_fclose(g);
    g = fnvnx_iob_fsopen("Data\\mode.txt", "r, ccs=UTF-8", 0x40);
    CHECK(g != NULL);
    fnvnx_iob_fclose(g);
    CHECK(fnvnx_iob_fgets(b, 0, fnvnx_iob) == NULL);
    CHECK_EQ(fnvnx_iob_fread(b, 0, 5, fnvnx_iob), 0u);

    /* 'D': removed when closed */
    f = fnvnx_iob_fopen("Data\\temp.txt", "wD");
    fnvnx_iob_fputs("t", f);
    CHECK(slurp("Data\\temp.txt") != "<missing>");
    fnvnx_iob_fclose(f);
    CHECK(slurp("Data\\temp.txt") == "<missing>");
}

TEST(iob_lowio)
{
    put_file("Data\\low.txt", std::string("a\r\nb\r\r\nc\x1a" "d"));
    int fd = fnvnx_open("Data\\low.txt", _O_RDONLY, 0); /* _fmode: text */
    CHECK(fd >= 3);
    char b[32];
    CHECK_EQ(fnvnx_read(fd, b, sizeof b), 6);
    CHECK(!memcmp(b, "a\nb\r\nc", 6));
    CHECK_EQ(fnvnx_read(fd, b, sizeof b), 0); /* Ctrl-Z */
    CHECK_EQ(fnvnx_tell(fd), 8);
    CHECK_EQ(fnvnx_lseek(fd, 1, SEEK_SET), 1);
    CHECK_EQ(fnvnx_read(fd, b, 1), 1); /* CR LF split by the request size: still one LF */
    CHECK_EQ(b[0], '\n');
    CHECK_EQ(fnvnx_setmode(fd, _O_BINARY), _O_TEXT);
    CHECK_EQ(fnvnx_lseek(fd, 0, SEEK_SET), 0);
    CHECK_EQ(fnvnx_read(fd, b, sizeof b), 10);
    CHECK_EQ(fnvnx_eof(fd), 1);
    CHECK_EQ(fnvnx_filelength(fd), 10);
    /* a stream on the descriptor follows its mode; fclose closes the descriptor */
    fnvnx_setmode(fd, _O_TEXT);
    fnvnx_lseek(fd, 0, SEEK_SET);
    _iobuf *f = fnvnx_iob_fdopen(fd, "rb");
    CHECK(f != NULL);
    CHECK_STR(fnvnx_iob_fgets(b, sizeof b, f), "a\n");
    CHECK_EQ(fnvnx_iob_fileno(f), fd);
    fnvnx_iob_fclose(f);
    CHECK_EQ(fnvnx_close(fd), -1);
    CHECK_EQ(errno, EBADF);

    fd = fnvnx_open("Data\\low2.txt", _O_WRONLY | _O_CREAT | _O_TRUNC | _O_TEXT, 0);
    CHECK_EQ(fnvnx_write(fd, "1\n2\n", 4), 4);
    CHECK_EQ(fnvnx_tell(fd), 6);
    CHECK_EQ(fnvnx_read(fd, b, 1), -1); /* write-only */
    CHECK_EQ(fnvnx_close(fd), 0);
    CHECK(slurp("Data\\low2.txt") == "1\r\n2\r\n");
    CHECK_EQ(fnvnx_open("Data\\low2.txt", _O_WRONLY | _O_CREAT | _O_EXCL, 0), -1);
    CHECK_EQ(errno, EEXIST);
    CHECK_EQ(fnvnx_open("Data\\nope.txt", _O_RDONLY, 0), -1);
    CHECK_EQ(errno, ENOENT);
    fd = fnvnx_open("Data\\low2.txt", _O_RDWR | _O_APPEND | _O_BINARY, 0);
    fnvnx_lseek(fd, 0, SEEK_SET);
    CHECK_EQ(fnvnx_write(fd, "3\n", 2), 2);
    CHECK_EQ(fnvnx_commit(fd), 0);
    fnvnx_close(fd);
    CHECK(slurp("Data\\low2.txt") == "1\r\n2\r\n3\n");
    CHECK_EQ(fnvnx_read(77, b, 1), -1);
    CHECK_EQ(errno, EBADF);
}

TEST(iob_overloads_for_game_code)
{
    /* the standard names with _iobuf* (C++ overloads next to libc's FILE* functions) */
    _iobuf *f = fnvnx_iob_fopen("Data\\ovl.txt", "w");
    fputs("x\n", f);
    fprintf(f, "%s=%d\n", "k", 3);
    fputc('!', f);
    CHECK_EQ(ftell(f), 9);
    CHECK_EQ(fclose(f), 0);
    CHECK(slurp("Data\\ovl.txt") == "x\r\nk=3\r\n!");
    CHECK_EQ(fopen_s(&f, "Data\\ovl.txt", "r"), 0);
    char b[16];
    CHECK_STR(fgets(b, sizeof b, f), "x\n");
    CHECK_EQ(fgetc(f), 'k');
    CHECK_EQ(ungetc('K', f), 'K');
    CHECK_EQ(getc(f), 'K');
    CHECK_EQ(fread(b, 1, 4, f), 4u);
    CHECK_EQ(fseek(f, 0, SEEK_SET), 0);
    CHECK_EQ(_ftelli64(f), 0);
    CHECK(!feof(f) && !ferror(f));
    rewind(f);
    CHECK_EQ(_fileno(f), f->_file);
    CHECK_EQ(fflush(f), 0);
    CHECK_EQ(fclose(f), 0);
    /* libc's FILE* functions are untouched */
    FILE *h = fopen((std::string(s4_root()) + "/game/Data/ovl.txt").c_str(), "rb");
    CHECK(h != NULL);
    if (h) {
        CHECK_EQ(fgetc(h), 'x');
        fclose(h);
    }
}
