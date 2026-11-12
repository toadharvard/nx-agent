cmake_minimum_required(VERSION 3.16)
project(fnvnx_shaders C CXX)
# Driven by build.sh (FNVNX_DEPS = pinned, patched MojoShader/uam checkouts, already built).
set(FNVNX_DEPS "" CACHE PATH "dependency dir from build.sh")
option(FNVNX_SHADERS_TESTS "build tests/s6b helpers" OFF)
set(CMAKE_C_STANDARD 99)
set(CMAKE_CXX_STANDARD 17)

set(MOJO ${FNVNX_DEPS}/mojoshader)
set(UAM ${FNVNX_DEPS}/uam)

add_library(fnvnx_shaders STATIC
    src/sdp.c src/frontend.c src/backend_mojoshader.c src/translate.c src/dk.cpp
    ../../lib/sha256/sha256.c)   # the content key: fnvnx_sha256, by path
target_include_directories(fnvnx_shaders PUBLIC include ../../lib/sha256/include PRIVATE src ${MOJO} ${UAM}/source)
target_link_libraries(fnvnx_shaders PUBLIC ${MOJO}/build/libmojoshader.a ${UAM}/build/libuam.a m pthread)

add_executable(sdp2dksh src/sdp2dksh.c)
target_link_libraries(sdp2dksh fnvnx_shaders)
set_target_properties(sdp2dksh PROPERTIES LINKER_LANGUAGE CXX)

if(FNVNX_SHADERS_TESTS)
    set(ASM ${FNVNX_DEPS}/mojoshader_assembler)
    set(T ${CMAKE_CURRENT_SOURCE_DIR}/../../tests/s6b)
    add_library(mojoasm STATIC ${ASM}/mojoshader_assembler_common.c ${ASM}/mojoshader_preprocessor.c
                               ${ASM}/mojoshader_lexer.c ${ASM}/mojoshader_assembler.c)
    target_include_directories(mojoasm PUBLIC ${ASM} ${MOJO})
    target_compile_definitions(mojoasm PRIVATE MOJOSHADER_NO_VERSION_INCLUDE)
    add_executable(s6b_asm ${T}/s6b_asm.c)
    target_link_libraries(s6b_asm mojoasm ${MOJO}/build/libmojoshader.a m)
    add_executable(s6b_unit ${T}/s6b_unit.c)
    target_link_libraries(s6b_unit fnvnx_shaders)
    set_target_properties(s6b_unit PROPERTIES LINKER_LANGUAGE CXX)
    add_executable(s6b_centroid ${T}/s6b_centroid.c)
    target_link_libraries(s6b_centroid fnvnx_shaders)
    set_target_properties(s6b_centroid PROPERTIES LINKER_LANGUAGE CXX)
endif()
