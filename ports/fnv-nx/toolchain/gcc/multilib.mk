MULTILIB_OPTIONS    = mabi=ilp32 fshort-wchar fsigned-char fPIC
MULTILIB_DIRNAMES   = ilp32 wchar16 schar pic
MULTILIB_REQUIRED   = fPIC mabi=ilp32/fshort-wchar/fsigned-char mabi=ilp32/fshort-wchar/fsigned-char/fPIC
MULTILIB_MATCHES    = fPIC=fpic fPIC=fpie fPIC=fPIE
