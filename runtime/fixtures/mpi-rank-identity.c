#ifndef _GNU_SOURCE
#define _GNU_SOURCE
#endif
#include <sched.h>
#include <stdio.h>
#include <stdlib.h>

/* Called from all language bindings; Linux CPU identity is independent of mpiP. */
void remora_rank_identity(int rank, int ranks) {
    cpu_set_t allowed;
    int cpu = sched_getcpu();
    if (sched_getaffinity(0, sizeof allowed, &allowed) != 0 || cpu < 0) exit(94);
    printf("AFFINITY %d %d %d\n", rank, cpu, CPU_COUNT(&allowed));
    fflush(stdout);
    (void) ranks;
}
