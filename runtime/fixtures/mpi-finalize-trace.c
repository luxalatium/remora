/* Test-only static-link tracing. No installed collector dependency. */
#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <unistd.h>

static void stage(const char *name)
{
    struct timespec stamp;
    const char *rank = getenv("OMPI_COMM_WORLD_RANK");
    if (rank == NULL)
        rank = getenv("PMI_RANK");
    clock_gettime(CLOCK_MONOTONIC, &stamp);
    fprintf(stderr, "LIFECYCLE rank=%s pid=%ld stage=%s mono=%ld.%09ld\n",
            rank == NULL ? "unknown" : rank, (long)getpid(), name,
            (long)stamp.tv_sec, stamp.tv_nsec);
    fflush(stderr);
}

extern int __real_MPI_Finalize(void);
extern int __real_PMPI_Finalize(void);

int __wrap_MPI_Finalize(void)
{
    int result;
    stage("MPI_Finalize.enter");
    result = __real_MPI_Finalize();
    stage("MPI_Finalize.return");
    return result;
}

int __wrap_PMPI_Finalize(void)
{
    int result;
    stage("PMPI_Finalize.enter");
    result = __real_PMPI_Finalize();
    stage("PMPI_Finalize.return");
    return result;
}
