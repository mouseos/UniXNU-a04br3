/* Host harness of package/A04BR3BootLog.c: writes snapshots into a simulated window and saves it for the decoder. */
#define A04BR3_BOOTLOG_HOST_TEST 1
#include "bootlog_host_stubs.h"
struct a04br3_msgbuf;
#include "../../package/A04BR3BootLog.c"
#include <stdlib.h>

static uint8_t window[8u << 20];
static int fail_header_write;
struct a04br3_msgbuf host_msgbuf;
struct a04br3_msgbuf *msgbufp = &host_msgbuf;
static char ring[4096];

int native_msdc_log_open(uint64_t blocks, uint64_t *first, const char **why) { (void)blocks; *first = 1234; *why = ""; return 1; }
int native_msdc_log_read(uint64_t relative, uint32_t blocks, void *d) { memcpy(d, window + relative * 4096, blocks * 4096u); return 1; }
int native_msdc_log_write(uint64_t relative, uint32_t blocks, const void *s)
{
	if (fail_header_write && blocks == 1 && relative % 256 == 0) return 0;   /* reset between data and header */
	if ((relative + blocks) * 4096 > sizeof(window)) abort();
	memcpy(window + relative * 4096, s, blocks * 4096u);
	return 1;
}
void IOSleep(unsigned ms) { (void)ms; }

static void put(const char *text)
{
	for (const char *c = text; *c; c++) { ring[host_msgbuf.msg_bufx] = *c; host_msgbuf.msg_bufx = (host_msgbuf.msg_bufx + 1) % (int)sizeof(ring); }
}

int main(int argc, char **argv)
{
	if (argc != 2) return 2;
	host_msgbuf.msg_magic = 0x063061; host_msgbuf.msg_size = sizeof(ring); host_msgbuf.msg_bufc = ring;
	a04br3_crc_init();
	/* boot 1: two snapshots (slots 2 and 3), the second wraps the ring */
	a04br3_bootlog_counter = a04br3_bootlog_previous() + 1;
	put("boot one line A\n");
	if (a04br3_bootlog_write() != 1) return 3;
	if (a04br3_bootlog_write() != 2) return 4;                         /* unchanged: not written again */
	for (int i = 0; i < 300; i++) put("boot one filler line that wraps the ring\n");
	put("boot one tail\n");
	if (a04br3_bootlog_write() != 1) return 5;
	/* boot 2: the counter continues; a reset between data and header keeps the previous snapshot of that slot pair */
	a04br3_bootlog_sequence = 0; a04br3_bootlog_last_crc = 0xffffffffu; a04br3_bootlog_last_bytes = 0;
	memset(ring, 0, sizeof(ring)); host_msgbuf.msg_bufx = 0;
	a04br3_bootlog_counter = a04br3_bootlog_previous() + 1;
	if (a04br3_bootlog_counter != 2) return 6;
	put("boot two first\n");
	if (a04br3_bootlog_write() != 1) return 7;
	put("boot two second\n");
	fail_header_write = 1;
	if (a04br3_bootlog_write() != 0) return 8;
	FILE *f = fopen(argv[1], "wb");
	if (!f || fwrite(window, 1, sizeof(window), f) != sizeof(window)) return 9;
	fclose(f);
	return 0;
}

