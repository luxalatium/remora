/* Native F08 profiling boundary; nested C wrappers use mpiP's TLS depth. */
#include "mpiPi.h"
#include "mpiPi_def.h"
#include <stdlib.h>
#include <string.h>

struct f08_span {
    mpiPi_mt_stat_tls_t *tls;
    mpiPi_TIME start;
    void *stack[MPIP_CALLSITE_STACK_DEPTH_MAX];
    int id, timed;
};

static int event_id(const char *name)
{
    if (strcmp(name, "MPI_Barrier") == 0) return mpiPi_MPI_Barrier;
    if (strcmp(name, "MPI_Send") == 0) return mpiPi_MPI_Send;
    if (strcmp(name, "MPI_Ssend") == 0) return mpiPi_MPI_Ssend;
    if (strcmp(name, "MPI_Bsend") == 0) return mpiPi_MPI_Bsend;
    if (strcmp(name, "MPI_Rsend") == 0) return mpiPi_MPI_Rsend;
    if (strcmp(name, "MPI_Recv") == 0) return mpiPi_MPI_Recv;
    if (strcmp(name, "MPI_Sendrecv") == 0) return mpiPi_MPI_Sendrecv;
    if (strcmp(name, "MPI_Isend") == 0) return mpiPi_MPI_Isend;
    if (strcmp(name, "MPI_Irecv") == 0) return mpiPi_MPI_Irecv;
    if (strcmp(name, "MPI_Wait") == 0) return mpiPi_MPI_Wait;
    if (strcmp(name, "MPI_Waitall") == 0) return mpiPi_MPI_Waitall;
    if (strcmp(name, "MPI_Bcast") == 0) return mpiPi_MPI_Bcast;
    if (strcmp(name, "MPI_Allreduce") == 0) return mpiPi_MPI_Allreduce;
    if (strcmp(name, "MPI_Iallreduce") == 0) return mpiPi_MPI_Iallreduce;
    if (strcmp(name, "MPI_Reduce") == 0) return mpiPi_MPI_Reduce;
    if (strcmp(name, "MPI_Allgather") == 0) return mpiPi_MPI_Allgather;
    if (strcmp(name, "MPI_Alltoall") == 0) return mpiPi_MPI_Alltoall;
    if (strcmp(name, "MPI_Comm_split") == 0) return mpiPi_MPI_Comm_split;
    if (strcmp(name, "MPI_File_read_at_all") == 0) return mpiPi_MPI_File_read_at_all;
    if (strcmp(name, "MPI_File_write_at_all") == 0) return mpiPi_MPI_File_write_at_all;
    if (strcmp(name, "MPI_File_read_at") == 0) return mpiPi_MPI_File_read_at;
    if (strcmp(name, "MPI_File_write_at") == 0) return mpiPi_MPI_File_write_at;
    if (strcmp(name, "MPI_File_open") == 0) return mpiPi_MPI_File_open;
    if (strcmp(name, "MPI_File_close") == 0) return mpiPi_MPI_File_close;
    if (strcmp(name, "MPI_File_sync") == 0) return mpiPi_MPI_File_sync;
    if (strcmp(name, "MPI_File_set_size") == 0) return mpiPi_MPI_File_set_size;
    if (strcmp(name, "MPI_Win_create") == 0) return mpiPi_MPI_Win_create;
    if (strcmp(name, "MPI_Win_fence") == 0) return mpiPi_MPI_Win_fence;
    if (strcmp(name, "MPI_Win_free") == 0) return mpiPi_MPI_Win_free;
    if (strcmp(name, "MPI_Put") == 0) return mpiPi_MPI_Put;
    return -1;
}

void *mpip_f08_begin(const char *name)
{
    struct f08_span *span = calloc(1, sizeof(*span));
    jmp_buf jbuf;
    if (span == NULL) {
        mpiPi_msg_warn("F08 profiling span allocation failed; call not recorded\n");
        return NULL;
    }
    span->tls = mpiPi_stats_mt_gettls(&mpiPi.task_stats);
    span->id = event_id(name);
    span->timed = mpiPi_stats_mt_is_on(span->tls);
    if (span->timed) {
        mpiPi_GETTIME(&span->start);
        if (mpiPi.reportStackDepth > 0)
            mpiPi_RecordTraceBack(jbuf, span->stack, mpiPi.fullStackDepth);
    }
    mpiPi_stats_mt_enter(span->tls);
    return span;
}

void mpip_f08_end(void *token, double mess, double io, double rma)
{
    struct f08_span *span = token;
    mpiPi_TIME end;
    double duration;
    if (span == NULL) return;
    mpiPi_stats_mt_exit(span->tls);
    if (span->timed && mpiPi_stats_mt_is_on(span->tls) && span->id >= 0) {
        mpiPi_GETTIME(&end);
        duration = mpiPi_GETTIMEDIFF(&end, &span->start);
        if (duration >= 0)
            mpiPi_update_callsite_stats(span->tls, span->id, mpiPi.rank,
                                       span->stack, duration, mess, io, rma);
    }
    free(span);
}
