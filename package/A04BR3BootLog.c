// SPDX-License-Identifier: BSD-2-Clause-Patent
// Persistent boot log of the a04br3 board (UniXNU log-capture guide, "persistent logs"), opt-in: native-msdc-log=1 (one
// kernel argument for the NativeMsdc window and this writer; the command line is near its 1000-byte limit).
//
// Writer side. The NativeMsdc boot log window (native-msdc-log=1, the last blocks of userdata; NativeMsdc.h logWindow) receives
// the kernel message buffer, linearised oldest first, every A04BR3_BOOTLOG_PERIOD seconds and once the window opens. Data
// blocks are written before the header, so a header always describes data that is already on the medium. The reader is the
// board firmware on the next boot (uefi-a04br3, A04BR3_LOG_READER) and the decoder scripts/read-a04br3-log.py.
//
// Window layout (4 KiB blocks): block 0 = header, blocks 1.. = message bytes (log_bytes, zero padded).
// Header (little endian): "A04BLOG1", u32 version 1, u32 header_bytes 64, u64 boot_id, u64 sequence, u64 uptime_ns,
// u32 log_bytes, u32 msgbuf_size, u32 log_crc32, u32 flags (bit 0: final write), u32 window_blocks, u32 header_crc32
// (over bytes 0..59).
#include <kern/thread.h>
#include <kern/clock.h>
#include <mach/mach_time.h>
#include <pexpert/pexpert.h>
#include <string.h>

#define A04BR3_BOOTLOG_BLOCK 4096u
#define A04BR3_BOOTLOG_WINDOW_BLOCKS 2048u                 /* 8 MiB, inside the NativeMsdc store reserve */
#define A04BR3_BOOTLOG_MAX_LOG (512u * 1024u)              /* larger message buffers keep their newest 512 KiB */
#define A04BR3_BOOTLOG_PERIOD 60u

struct a04br3_msgbuf { int msg_magic, msg_size, msg_bufx, msg_bufr; char *msg_bufc; };   /* bsd/sys/msgbuf.h */
extern struct a04br3_msgbuf *msgbufp;
extern int native_msdc_log_open(uint64_t blocks, uint64_t *first_sector, const char **why);
extern int native_msdc_log_write(uint64_t relative, uint32_t blocks, const void *source);
extern void IOSleep(unsigned milliseconds);

static uint8_t a04br3_bootlog_buffer[A04BR3_BOOTLOG_BLOCK + A04BR3_BOOTLOG_MAX_LOG] __attribute__((aligned(64)));
static uint64_t a04br3_bootlog_id, a04br3_bootlog_sequence;

static uint32_t a04br3_crc32(const uint8_t *data, uint32_t length)
{
	uint32_t crc = 0xffffffffu;
	for (uint32_t i = 0; i < length; i++) {
		crc ^= data[i];
		for (unsigned bit = 0; bit < 8; bit++) crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
	}
	return ~crc;
}

static void a04br3_put32(uint8_t *at, uint32_t value) { for (unsigned i = 0; i < 4; i++) at[i] = (uint8_t)(value >> (8 * i)); }
static void a04br3_put64(uint8_t *at, uint64_t value) { for (unsigned i = 0; i < 8; i++) at[i] = (uint8_t)(value >> (8 * i)); }

/* Copies the message buffer oldest first into the data area; returns the number of bytes. */
static uint32_t a04br3_bootlog_snapshot(uint32_t *size_out)
{
	struct a04br3_msgbuf *mb = msgbufp;
	uint8_t *data = a04br3_bootlog_buffer + A04BR3_BOOTLOG_BLOCK;
	*size_out = 0;
	if (!mb || mb->msg_magic != 0x063061 || mb->msg_size <= 0 || !mb->msg_bufc) return 0;
	uint32_t size = (uint32_t)mb->msg_size;
	uint32_t write = (uint32_t)mb->msg_bufx % size;     /* unlocked snapshot: a torn line at the seam is acceptable */
	*size_out = size;
	uint32_t keep = size < A04BR3_BOOTLOG_MAX_LOG ? size : A04BR3_BOOTLOG_MAX_LOG;
	uint32_t start = (write + size - keep) % size, length = 0;
	for (uint32_t i = 0; i < keep; i++) {
		char c = mb->msg_bufc[(start + i) % size];
		if (c) data[length++] = (uint8_t)c;               /* the never-written part of a young buffer is zero */
	}
	return length;
}

static int a04br3_bootlog_write(uint32_t flags)
{
	uint32_t msgbuf_size = 0;
	uint32_t length = a04br3_bootlog_snapshot(&msgbuf_size);
	uint8_t *header = a04br3_bootlog_buffer, *data = a04br3_bootlog_buffer + A04BR3_BOOTLOG_BLOCK;
	uint32_t blocks = (length + A04BR3_BOOTLOG_BLOCK - 1) / A04BR3_BOOTLOG_BLOCK;
	memset(data + length, 0, blocks * A04BR3_BOOTLOG_BLOCK - length);
	if (blocks && !native_msdc_log_write(1, blocks, data)) return 0;
	uint64_t now_ns = 0;
	absolutetime_to_nanoseconds(mach_absolute_time(), &now_ns);
	memset(header, 0, A04BR3_BOOTLOG_BLOCK);
	memcpy(header, "A04BLOG1", 8);
	a04br3_put32(header + 8, 1);
	a04br3_put32(header + 12, 64);
	a04br3_put64(header + 16, a04br3_bootlog_id);
	a04br3_put64(header + 24, ++a04br3_bootlog_sequence);
	a04br3_put64(header + 32, now_ns);
	a04br3_put32(header + 40, length);
	a04br3_put32(header + 44, msgbuf_size);
	a04br3_put32(header + 48, a04br3_crc32(data, length));
	a04br3_put32(header + 52, flags);
	a04br3_put32(header + 56, A04BR3_BOOTLOG_WINDOW_BLOCKS);
	a04br3_put32(header + 60, a04br3_crc32(header, 60));
	return native_msdc_log_write(0, 1, header);
}

static void a04br3_bootlog_thread(void *arg __unused, wait_result_t wr __unused)
{
	const char *why = "";
	uint64_t first = 0;
	/* NativeMsdc matches after bsd_init's board hook: wait up to 5 minutes for its root layout. */
	for (unsigned attempt = 0; attempt < 150; attempt++) {
		if (native_msdc_log_open(A04BR3_BOOTLOG_WINDOW_BLOCKS, &first, &why)) break;
		if (!strcmp(why, "off") || !strcmp(why, "already open") || !strcmp(why, "size") ||
		    !strcmp(why, "outside the store reserve")) {
			printf("a04br3 bootlog: window refused: %s\n", why);
			return;
		}
		IOSleep(2000);
	}
	if (!first) { printf("a04br3 bootlog: window not opened: %s\n", why); return; }
	printf("a04br3 bootlog: boot %016llx at sector %llu\n", a04br3_bootlog_id, first);
	for (;;) {
		if (!a04br3_bootlog_write(0)) { printf("a04br3 bootlog: write failed, writer stops\n"); return; }
		IOSleep(A04BR3_BOOTLOG_PERIOD * 1000);
	}
}

void a04br3_bootlog_start(void)
{
	unsigned enabled = 0;
	if (!PE_parse_boot_argn("native-msdc-log", &enabled, sizeof(enabled)) || enabled != 1) return;
	clock_sec_t seconds = 0;
	clock_usec_t micros = 0;
	clock_get_calendar_microtime(&seconds, &micros);
	a04br3_bootlog_id = ((uint64_t)seconds << 20) ^ (uint64_t)micros ^ mach_absolute_time();
	thread_t thread;
	if (kernel_thread_start(a04br3_bootlog_thread, NULL, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

