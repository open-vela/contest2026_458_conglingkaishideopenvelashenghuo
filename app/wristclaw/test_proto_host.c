/* Host-side interop test for wc_proto.c (no NuttX dependency in the core).
 *
 * Phase 1: read frames_pc.bin (built by Python protocol.build_frame),
 *          parse with wc_parser, verify count/type/seq/payload.
 *          A corrupted frame + garbage is spliced in: the parser must
 *          resync and still find every good frame after it.
 * Phase 2: build reply frames with wc_build() -> frames_dev.bin,
 *          which the Python side then parses with FrameParser.
 *
 * argv[1] = frames_pc.bin  argv[2] = frames_dev.bin
 */
#include "wc_proto.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static uint8_t g_buf[1 << 16];

int main(int argc, char **argv)
{
  FILE *fp;
  size_t n;
  struct wc_parser_s p;
  struct wc_frame_s f;
  int nframes;
  int failures = 0;
  static const uint16_t expect_seq[8] =
    { 1, 2, 3, 7777, 0xbeef, 900, 901, 902 };

  if (argc < 3)
    {
      fprintf(stderr, "usage: %s frames_pc.bin frames_dev.bin\n", argv[0]);
      return 2;
    }

  fp = fopen(argv[1], "rb");
  if (!fp)
    {
      perror(argv[1]);
      return 2;
    }
  n = fread(g_buf, 1, sizeof(g_buf), fp);
  fclose(fp);
  printf("[C] read %zu bytes from %s\n", n, argv[1]);

  wc_parser_init(&p);
  wc_parser_push(&p, g_buf, n);

  nframes = 0;
  while (wc_parser_next(&p, &f) == 1)
    {
      printf("[C] frame: type=0x%02x seq=%u len=%u head='%c%c'\n",
             f.type, f.seq, f.len,
             f.len > 0 ? f.payload[0] : '.',
             f.len > 1 ? f.payload[1] : '.');
      if (f.seq != expect_seq[nframes])
        {
          printf("[C] FAIL: seq mismatch, expected %u\n",
                 expect_seq[nframes]);
          failures++;
        }
      nframes++;
    }

  /* 8 good frames + 1 deliberately corrupted (must be dropped) */
  if (nframes != 8)
    {
      printf("[C] FAIL: expected 8 good frames, got %d\n", nframes);
      failures++;
    }
  else
    {
      printf("[C] PASS: 8/8 frames parsed, corrupted frame resynced out\n");
    }

  /* Phase 2: build frames for the Python side to verify */
  fp = fopen(argv[2], "wb");
  if (!fp)
    {
      perror(argv[2]);
      return 2;
    }

  {
    struct
    {
      uint8_t type;
      const char *payload;
    } replies[3] =
    {
      { WC_T_DEVICE_STATUS, "brightness=70 reminders=2 skills=1" },
      { WC_T_ACK,           "" },
      { WC_T_TEXT,          "\xe4\xbd\xa0\xe5\xa5\xbd\xef\xbc\x8c"
                            "\xe4\xb8\x96\xe7\x95\x8c" },  /* UTF-8 你好，世界 */
    };
    int i;

    for (i = 0; i < 3; i++)
      {
        uint8_t out[WC_MAX_FRAME];
        size_t  plen = strlen(replies[i].payload);
        size_t  flen;

        flen = wc_build(out, sizeof(out), replies[i].type,
                        (uint16_t)(100 + i),
                        (const uint8_t *)replies[i].payload, plen);
        if (flen == 0)
          {
            printf("[C] FAIL: wc_build returned 0 for frame %d\n", i);
            failures++;
            continue;
          }
        fwrite(out, 1, flen, fp);
        printf("[C] built: type=0x%02x seq=%u len=%zu -> %zu bytes\n",
               replies[i].type, 100 + i, plen, flen);
      }
  }
  fclose(fp);

  printf("[C] %s (%d failures)\n", failures ? "FAIL" : "ALL OK", failures);
  return failures ? 1 : 0;
}
