// SPDX-License-Identifier: BSD-2-Clause-Patent
// Persistent boot log of the a04br3 board (UniXNU log-capture guide, "persistent logs"), opt-in: native-msdc-log=1 (one
// kernel argument for the NativeMsdc window and this writer; the command line is near its 1000-byte limit).
//
// Writer side. The NativeMsdc boot log window (the last 8 MiB of userdata; NativeMsdc.h logWindow) holds 8 slots of 1 MiB
// (256 blocks of 4 KiB): block 0 of a slot is the header, blocks 1.. the kernel message buffer, oldest first. Boot N writes
// slots 2*(N%4) and 2*(N%4)+1 alternately, so the last 4 boots stay readable and a reset during a write only loses the
// snapshot being written: the other slot of the boot keeps the previous one. N is one more than the highest counter in the
// window. The reader is the board firmware on the next boot (uefi-a04br3 A04BR3BootLogReaderDxe), the decoder
// scripts/read-a04br3-log.py; both take the newest slot whose header and log CRC verify.
//
// Header (128 bytes, little endian): 0 "A04BLOG1", 8 u32 version 2, 12 u32 header_bytes 128, 16 u64 boot_counter,
// 24 u64 sequence, 32 u64 uptime_ns, 40 u32 log_bytes, 44 u32 msgbuf_size, 48 u32 log_crc32, 52 u32 flags (bit 0 final,
// bit 1 panic, bit 2 an earlier write of this boot failed), 56 u32 window_blocks 2048, 60 u32 slot, 64 char kernel_uuid[40]
// (the kernel's UUID string: which image wrote it), 104..123 zero, 124 u32 header_crc32 over bytes 0..123.
#ifndef A04BR3_BOOTLOG_HOST_TEST   /* scripts/tests/test_bootlog.py compiles this file with host stubs */
#include <kern/thread.h>
#include <kern/clock.h>
#include <kern/debug.h>
#include <mach/mach_time.h>
#include <pexpert/pexpert.h>
#include <string.h>
#endif

#define A04BR3_BOOTLOG_BLOCK 4096u
#define A04BR3_BOOTLOG_WINDOW_BLOCKS 2048u                 /* 8 MiB, inside the NativeMsdc store reserve */
#define A04BR3_BOOTLOG_SLOT_BLOCKS 256u                    /* 8 slots of 1 MiB */
#define A04BR3_BOOTLOG_SLOTS (A04BR3_BOOTLOG_WINDOW_BLOCKS / A04BR3_BOOTLOG_SLOT_BLOCKS)
#define A04BR3_BOOTLOG_MAX_LOG (512u * 1024u)              /* larger message buffers keep their newest 512 KiB */
#define A04BR3_BOOTLOG_HEADER 128u

struct a04br3_msgbuf { int msg_magic, msg_size, msg_bufx, msg_bufr; char *msg_bufc; };   /* bsd/sys/msgbuf.h */
extern struct a04br3_msgbuf *msgbufp;
extern int native_msdc_log_open(uint64_t blocks, uint64_t *first_sector, const char **why);
extern int native_msdc_log_read(uint64_t relative, uint32_t blocks, void *destination);
extern int native_msdc_log_write(uint64_t relative, uint32_t blocks, const void *source);
extern void IOSleep(unsigned milliseconds);

static uint8_t a04br3_bootlog_buffer[A04BR3_BOOTLOG_BLOCK + A04BR3_BOOTLOG_MAX_LOG] __attribute__((aligned(64)));
static uint64_t a04br3_bootlog_counter, a04br3_bootlog_sequence;
static uint32_t a04br3_bootlog_flags, a04br3_bootlog_last_crc = 0xffffffffu, a04br3_bootlog_last_bytes;
static uint32_t a04br3_crc_table[256];

static void a04br3_crc_init(void)
{
	for (uint32_t n = 0; n < 256; n++) {
		uint32_t c = n;
		for (unsigned bit = 0; bit < 8; bit++) c = (c >> 1) ^ (0xedb88320u & (0u - (c & 1u)));
		a04br3_crc_table[n] = c;
	}
}

static uint32_t a04br3_crc32(const uint8_t *data, uint32_t length)
{
	uint32_t crc = 0xffffffffu;
	for (uint32_t i = 0; i < length; i++) crc = a04br3_crc_table[(crc ^ data[i]) & 0xffu] ^ (crc >> 8);
	return ~crc;
}

static void a04br3_put32(uint8_t *at, uint32_t value) { for (unsigned i = 0; i < 4; i++) at[i] = (uint8_t)(value >> (8 * i)); }
static void a04br3_put64(uint8_t *at, uint64_t value) { for (unsigned i = 0; i < 8; i++) at[i] = (uint8_t)(value >> (8 * i)); }
static uint32_t a04br3_get32(const uint8_t *at) { return (uint32_t)at[0] | (uint32_t)at[1] << 8 | (uint32_t)at[2] << 16 | (uint32_t)at[3] << 24; }
static uint64_t a04br3_get64(const uint8_t *at) { return (uint64_t)a04br3_get32(at) | (uint64_t)a04br3_get32(at + 4) << 32; }

/* Highest boot counter among the slots whose header verifies (0 when none). */
static uint64_t a04br3_bootlog_previous(void)
{
	uint8_t *header = a04br3_bootlog_buffer;
	uint64_t highest = 0;
	for (uint32_t slot = 0; slot < A04BR3_BOOTLOG_SLOTS; slot++) {
		if (!native_msdc_log_read(slot * A04BR3_BOOTLOG_SLOT_BLOCKS, 1, header)) continue;
		if (memcmp(header, "A04BLOG1", 8) || a04br3_get32(header + 8) != 2 || a04br3_get32(header + 12) != A04BR3_BOOTLOG_HEADER ||
		    a04br3_crc32(header, 124) != a04br3_get32(header + 124))
			continue;
		uint64_t counter = a04br3_get64(header + 16);
		if (counter > highest) highest = counter;
	}
	return highest;
}

/* Copies the message buffer oldest first into the data area; returns the number of bytes. */
static uint32_t a04br3_bootlog_snapshot(uint32_t *size_out)
{
	struct a04br3_msgbuf *mb = msgbufp;
	uint8_t *data = a04br3_bootlog_buffer + A04BR3_BOOTLOG_BLOCK;
	*size_out = 0;
	if (!mb || mb->msg_magic != 0x063061 || mb->msg_size <= 0 || !mb->msg_bufc) return 0;
	uint32_t size = (uint32_t)mb->msg_size;
	/* Unlocked copy: a torn line at the seam is acceptable; the CRC covers what is stored. */
	uint32_t write = (uint32_t)mb->msg_bufx % size;
	*size_out = size;
	uint32_t keep = size < A04BR3_BOOTLOG_MAX_LOG ? size : A04BR3_BOOTLOG_MAX_LOG;
	uint32_t start = (write + size - keep) % size, length = 0;
	for (uint32_t i = 0, at = start; i < keep; i++, at = at + 1 == size ? 0 : at + 1) {
		char c = mb->msg_bufc[at];
		if (c) data[length++] = (uint8_t)c;               /* the never-written part of a young buffer is zero */
	}
	return length;
}

/* 1 written, 2 unchanged since the last write, 0 failed. */
static int a04br3_bootlog_write(void)
{
	uint32_t msgbuf_size = 0;
	uint32_t length = a04br3_bootlog_snapshot(&msgbuf_size);
	uint8_t *header = a04br3_bootlog_buffer, *data = a04br3_bootlog_buffer + A04BR3_BOOTLOG_BLOCK;
	uint32_t crc = a04br3_crc32(data, length);
	if (length == a04br3_bootlog_last_bytes && crc == a04br3_bootlog_last_crc) return 2;
	uint64_t sequence = a04br3_bootlog_sequence + 1;
	uint32_t slot = (uint32_t)((a04br3_bootlog_counter % (A04BR3_BOOTLOG_SLOTS / 2)) * 2 + (sequence & 1));
	uint64_t base = (uint64_t)slot * A04BR3_BOOTLOG_SLOT_BLOCKS;
	uint32_t blocks = (length + A04BR3_BOOTLOG_BLOCK - 1) / A04BR3_BOOTLOG_BLOCK;
	memset(data + length, 0, blocks * A04BR3_BOOTLOG_BLOCK - length);
	if (blocks && !native_msdc_log_write(base + 1, blocks, data)) return 0;
	uint64_t now_ns = 0;
	absolutetime_to_nanoseconds(mach_absolute_time(), &now_ns);
	memset(header, 0, A04BR3_BOOTLOG_BLOCK);
	memcpy(header, "A04BLOG1", 8);
	a04br3_put32(header + 8, 2);
	a04br3_put32(header + 12, A04BR3_BOOTLOG_HEADER);
	a04br3_put64(header + 16, a04br3_bootlog_counter);
	a04br3_put64(header + 24, sequence);
	a04br3_put64(header + 32, now_ns);
	a04br3_put32(header + 40, length);
	a04br3_put32(header + 44, msgbuf_size);
	a04br3_put32(header + 48, crc);
	a04br3_put32(header + 52, a04br3_bootlog_flags);
	a04br3_put32(header + 56, A04BR3_BOOTLOG_WINDOW_BLOCKS);
	a04br3_put32(header + 60, slot);
	strlcpy((char *)header + 64, kernel_uuid_string, 40);
	a04br3_put32(header + 124, a04br3_crc32(header, 124));
	if (!native_msdc_log_write(base, 1, header)) return 0;
	a04br3_bootlog_sequence = sequence;
	a04br3_bootlog_last_crc = crc;
	a04br3_bootlog_last_bytes = length;
	return 1;
}

static void a04br3_bootlog_thread(void *arg __unused, wait_result_t wr __unused)
{
	const char *why = "";
	uint64_t first = 0;
	/* NativeMsdc matches after bsd_init's board hook: wait up to 5 minutes for its root layout. */
	for (unsigned attempt = 0; attempt < 150 && !first; attempt++) {
		if (native_msdc_log_open(A04BR3_BOOTLOG_WINDOW_BLOCKS, &first, &why)) break;
		if (strcmp(why, "no root layout")) break;     /* every other refusal is final */
		IOSleep(2000);
	}
	if (!first) { printf("a04br3 bootlog: window refused: %s\n", why); return; }
	a04br3_bootlog_counter = a04br3_bootlog_previous() + 1;
	unsigned long long pair = (unsigned long long)(a04br3_bootlog_counter % (A04BR3_BOOTLOG_SLOTS / 2)) * 2;
	printf("a04br3 bootlog: boot %llu (kernel %s) at sector %llu, slots %llu and %llu\n",
	    (unsigned long long)a04br3_bootlog_counter, kernel_uuid_string, (unsigned long long)first, pair, pair + 1);
	uint64_t start_ns = 0, now_ns = 0;
	absolutetime_to_nanoseconds(mach_absolute_time(), &start_ns);
	unsigned failures = 0;
	for (;;) {
		int result = a04br3_bootlog_write();
		if (!result) {
			a04br3_bootlog_flags |= 4u;
			if (++failures >= 3) { printf("a04br3 bootlog: 3 writes failed in a row, writer stops\n"); return; }
		} else {
			failures = 0;
		}
		/* 10 s during the first 10 minutes of the writer, then 60 s; unchanged snapshots are not written again. */
		absolutetime_to_nanoseconds(mach_absolute_time(), &now_ns);
		IOSleep(now_ns - start_ns < 600ull * 1000000000ull ? 10000 : 60000);
	}
}

void a04br3_bootlog_start(void)
{
	unsigned enabled = 0;
	if (!PE_parse_boot_argn("native-msdc-log", &enabled, sizeof(enabled)) || enabled != 1) return;
	a04br3_crc_init();
	thread_t thread;
	if (kernel_thread_start(a04br3_bootlog_thread, NULL, &thread) == KERN_SUCCESS) thread_deallocate(thread);
}

