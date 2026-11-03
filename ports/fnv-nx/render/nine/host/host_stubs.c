/* fnv-nx S6d: symbols the host shader path references but never reaches.
 * nine_shader.c's NIR branch (screen PREFERRED_IR is TGSI on nvc0, see nvc0_fake_screen.c). */
#include <stdio.h>
#include <stdlib.h>

struct nir_shader;
void nir_print_shader(struct nir_shader *shader, FILE *fp);

void
nir_print_shader(struct nir_shader *shader, FILE *fp)
{
   (void)shader; (void)fp;
   abort();
}

struct pipe_screen;
struct nir_shader *tgsi_to_nir(const void *tgsi_tokens, struct pipe_screen *screen);

struct nir_shader *
tgsi_to_nir(const void *tgsi_tokens, struct pipe_screen *screen)
{
   (void)tgsi_tokens; (void)screen;
   abort();
}
