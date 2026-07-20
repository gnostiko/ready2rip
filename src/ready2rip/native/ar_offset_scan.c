/*
 * Fast AccurateRip offset scanner for ready2rip.
 * Reads little-endian uint32 stereo frames from a raw file and finds a
 * sample offset whose AR v1/v2 CRC matches one of the target checksums.
 *
 * Usage:
 *   ar_offset_scan <raw_path> <n_samples> <track> <total>
 *                  <lo> <hi> <step> <target_hex> [target_hex ...]
 *
 * Prints: MATCH <offset> <v1_hex> <v2_hex>
 * or:     NONE
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#define SKIP_FRAMES (5 * 588)

static void compute(
	const uint32_t *samples,
	size_t n,
	unsigned track,
	unsigned total,
	int offset,
	uint32_t *v1,
	uint32_t *v2)
{
	/* Match whipper: check_from starts at 0, MulBy at 1. */
	size_t check_from = 0;
	size_t check_to = n;
	uint32_t csum_hi = 0;
	uint32_t csum_lo = 0;
	size_t i;

	if (track == 1)
		check_from += SKIP_FRAMES;
	if (track == total)
		check_to -= SKIP_FRAMES;

	for (i = 0; i < n; i++) {
		size_t mul = i + 1;
		int64_t src;
		uint32_t word;
		uint64_t product;

		if (mul < check_from || mul > check_to)
			continue;
		src = (int64_t)i + (int64_t)offset;
		word = (src >= 0 && (size_t)src < n) ? samples[src] : 0;
		product = (uint64_t)word * (uint64_t)mul;
		csum_hi += (uint32_t)(product >> 32);
		csum_lo += (uint32_t)product;
	}
	*v1 = csum_lo;
	*v2 = csum_lo + csum_hi;
}

int main(int argc, char **argv)
{
	const char *path;
	size_t n_samples;
	unsigned track, total;
	int lo, hi, step;
	uint32_t *targets;
	int n_targets;
	FILE *fp;
	uint32_t *samples;
	size_t got;
	int off;
	int t;

	if (argc < 9) {
		fprintf(stderr, "usage: ar_offset_scan raw n track total lo hi step targets...\n");
		return 2;
	}

	path = argv[1];
	n_samples = (size_t)strtoull(argv[2], NULL, 10);
	track = (unsigned)strtoul(argv[3], NULL, 10);
	total = (unsigned)strtoul(argv[4], NULL, 10);
	lo = (int)strtol(argv[5], NULL, 10);
	hi = (int)strtol(argv[6], NULL, 10);
	step = (int)strtol(argv[7], NULL, 10);
	if (step == 0)
		step = 1;
	n_targets = argc - 8;
	targets = calloc((size_t)n_targets, sizeof(uint32_t));
	if (!targets)
		return 1;
	for (t = 0; t < n_targets; t++)
		targets[t] = (uint32_t)strtoul(argv[8 + t], NULL, 16);

	fp = fopen(path, "rb");
	if (!fp) {
		perror(path);
		free(targets);
		return 1;
	}
	samples = malloc(n_samples * sizeof(uint32_t));
	if (!samples) {
		fclose(fp);
		free(targets);
		return 1;
	}
	got = fread(samples, sizeof(uint32_t), n_samples, fp);
	fclose(fp);
	if (got != n_samples) {
		fprintf(stderr, "short read: %zu / %zu\n", got, n_samples);
		free(samples);
		free(targets);
		return 1;
	}

	for (off = lo; off <= hi; off += step) {
		uint32_t v1, v2;
		compute(samples, n_samples, track, total, off, &v1, &v2);
		for (t = 0; t < n_targets; t++) {
			if (targets[t] == v1 || targets[t] == v2) {
				printf("MATCH %d %08x %08x\n", off, v1, v2);
				free(samples);
				free(targets);
				return 0;
			}
		}
	}

	printf("NONE\n");
	free(samples);
	free(targets);
	return 0;
}
