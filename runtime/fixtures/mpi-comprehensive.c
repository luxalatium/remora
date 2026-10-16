/* Deterministic MPI correctness workload; profiler expectations are external. */
#include <mpi.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef __cplusplus
#include <numeric>
#include <vector>
extern "C" void remora_rank_identity(int, int);
#define LANGUAGE "cxx"
#else
extern void remora_rank_identity(int, int);
#define LANGUAGE "c"
#endif

enum { LOOPS = 4, MAX_RANKS = 64 };
static int rank_id, ranks, errors;

static void check(int condition) { if (!condition) ++errors; }
static void mpi_ok(int rc) {
    if (rc != MPI_SUCCESS) {
        fprintf(stderr, "UNEXPECTED_MPI_ERROR rank=%d rc=%d\n", rank_id, rc);
        MPI_Abort(MPI_COMM_WORLD, 90);
    }
}

static void core(void) {
    int send[64], recv[64], gathered[MAX_RANKS], exchanged[MAX_RANKS];
    int next = (rank_id + 1) % ranks, prev = (rank_id + ranks - 1) % ranks;
    int i, k, value, total, subrank, subsize;
    MPI_Comm sub;
    MPI_Datatype stride;
    MPI_Request requests[2], request;
    mpi_ok(MPI_Barrier(MPI_COMM_WORLD));
    for (k = 0; k < LOOPS; ++k) {
        for (i = 0; i < 64; ++i) { send[i] = rank_id * 100 + i + k * 1000; recv[i] = -9; }
        if ((rank_id ^ 1) < ranks) {
            int peer = rank_id ^ 1;
            if (rank_id % 2 == 0) {
                mpi_ok(MPI_Send(send, 4, MPI_INT, peer, 10, MPI_COMM_WORLD));
                mpi_ok(MPI_Recv(recv, 4, MPI_INT, peer, 11, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
            } else {
                mpi_ok(MPI_Recv(recv, 4, MPI_INT, peer, 10, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
                mpi_ok(MPI_Send(send, 4, MPI_INT, peer, 11, MPI_COMM_WORLD));
            }
            for (i = 0; i < 4; ++i) check(recv[i] == peer * 100 + i + k * 1000);
        }
        mpi_ok(MPI_Sendrecv(send, 8, MPI_INT, next, 20, recv, 8, MPI_INT,
                            prev, 20, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
        for (i = 0; i < 8; ++i) check(recv[i] == prev * 100 + i + k * 1000);
        mpi_ok(MPI_Sendrecv(send, 0, MPI_INT, next, 21, recv, 0, MPI_INT,
                            prev, 21, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
        mpi_ok(MPI_Irecv(recv, 64, MPI_INT, prev, 30, MPI_COMM_WORLD, &requests[0]));
        mpi_ok(MPI_Isend(send, 64, MPI_INT, next, 30, MPI_COMM_WORLD, &requests[1]));
        mpi_ok(MPI_Waitall(2, requests, MPI_STATUSES_IGNORE));
        for (i = 0; i < 64; ++i) check(recv[i] == prev * 100 + i + k * 1000);
        value = rank_id == 0 ? 100 + k : -1;
        mpi_ok(MPI_Bcast(&value, 1, MPI_INT, 0, MPI_COMM_WORLD));
        check(value == 100 + k);
        value = rank_id + 1; total = value;
        if (k % 2 == 0) mpi_ok(MPI_Allreduce(MPI_IN_PLACE, &total, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD));
        else mpi_ok(MPI_Allreduce(&value, &total, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD));
        check(total == ranks * (ranks + 1) / 2);
        mpi_ok(MPI_Allgather(&rank_id, 1, MPI_INT, gathered, 1, MPI_INT, MPI_COMM_WORLD));
        for (i = 0; i < ranks; ++i) { check(gathered[i] == i); gathered[i] = rank_id * 100 + i; }
        mpi_ok(MPI_Alltoall(gathered, 1, MPI_INT, exchanged, 1, MPI_INT, MPI_COMM_WORLD));
        for (i = 0; i < ranks; ++i) check(exchanged[i] == i * 100 + rank_id);
        mpi_ok(MPI_Iallreduce(&value, &total, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD, &request));
        mpi_ok(MPI_Wait(&request, MPI_STATUS_IGNORE));
        check(total == ranks * (ranks + 1) / 2);
    }
    mpi_ok(MPI_Comm_split(MPI_COMM_WORLD, rank_id % 2, rank_id, &sub));
    mpi_ok(MPI_Comm_rank(sub, &subrank)); mpi_ok(MPI_Comm_size(sub, &subsize));
    for (k = 0; k < LOOPS; ++k) {
        value = subrank + 1;
        mpi_ok(MPI_Allreduce(&value, &total, 1, MPI_INT, MPI_SUM, sub));
        check(total == subsize * (subsize + 1) / 2);
    }
    mpi_ok(MPI_Comm_free(&sub));
    mpi_ok(MPI_Type_vector(4, 1, 2, MPI_INT, &stride)); mpi_ok(MPI_Type_commit(&stride));
    for (k = 0; k < LOOPS; ++k) {
        for (i = 0; i < 8; ++i) { send[i] = rank_id * 100 + i; recv[i] = -9; }
        mpi_ok(MPI_Sendrecv(send, 1, stride, next, 40, recv, 1, stride,
                            prev, 40, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
        for (i = 0; i < 8; ++i) check(recv[i] == (i % 2 == 0 ? prev * 100 + i : -9));
    }
    mpi_ok(MPI_Type_free(&stride));
#ifdef __cplusplus
    std::vector<int> values(64, 3);
    check(std::accumulate(values.begin(), values.end(), 0) == 192);
#endif
}

static void io_case(const char *path) {
    MPI_File file;
    int send[16], recv[16], i, k, bytes;
    mpi_ok(MPI_Type_size(MPI_INT, &bytes));
    mpi_ok(MPI_File_open(MPI_COMM_WORLD, path, MPI_MODE_CREATE | MPI_MODE_RDWR, MPI_INFO_NULL, &file));
    mpi_ok(MPI_File_set_size(file, 0));
    for (k = 0; k < LOOPS; ++k) {
        for (i = 0; i < 16; ++i) send[i] = rank_id * 100 + i + k * 1000;
        MPI_Offset offset = (MPI_Offset) rank_id * 16 * bytes;
        mpi_ok(MPI_File_write_at_all(file, offset, send, 16, MPI_INT, MPI_STATUS_IGNORE));
        mpi_ok(MPI_File_sync(file));
        mpi_ok(MPI_File_read_at_all(file, offset, recv, 16, MPI_INT, MPI_STATUS_IGNORE));
        for (i = 0; i < 16; ++i) check(recv[i] == send[i]);
    }
    mpi_ok(MPI_File_close(&file));
}

static void rma_case(void) {
    int exposed = -1, outgoing, k, bytes, prev = (rank_id + ranks - 1) % ranks;
    MPI_Win window;
    mpi_ok(MPI_Type_size(MPI_INT, &bytes));
    mpi_ok(MPI_Win_create(&exposed, bytes, bytes, MPI_INFO_NULL, MPI_COMM_WORLD, &window));
    for (k = 0; k < LOOPS; ++k) {
        outgoing = rank_id + 1000 * k;
        mpi_ok(MPI_Win_fence(0, window));
        mpi_ok(MPI_Put(&outgoing, 1, MPI_INT, (rank_id + 1) % ranks, 0, 1, MPI_INT, window));
        mpi_ok(MPI_Win_fence(0, window));
        check(exposed == prev + 1000 * k);
    }
    mpi_ok(MPI_Win_free(&window));
}

static void error_case(void) {
    int value = 1, error_class, rc;
    mpi_ok(MPI_Comm_set_errhandler(MPI_COMM_WORLD, MPI_ERRORS_RETURN));
    rc = MPI_Send(&value, 0, MPI_INT, ranks, 80, MPI_COMM_WORLD);
    check(rc != MPI_SUCCESS);
    mpi_ok(MPI_Error_class(rc, &error_class)); check(error_class == MPI_ERR_RANK);
}

static void pcontrol_case(void) {
    int k, value = rank_id, received, prev = (rank_id + ranks - 1) % ranks;
    MPI_Pcontrol(0);
    for (k = 0; k < 2; ++k) mpi_ok(MPI_Sendrecv(&value, 1, MPI_INT, (rank_id + 1) % ranks,
        90, &received, 1, MPI_INT, prev, 90, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
    MPI_Pcontrol(2); MPI_Pcontrol(1);
    for (k = 0; k < LOOPS; ++k) {
        mpi_ok(MPI_Sendrecv(&value, 1, MPI_INT, (rank_id + 1) % ranks,
            91, &received, 1, MPI_INT, prev, 91, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
        check(received == prev);
    }
    MPI_Pcontrol(3); MPI_Pcontrol(0);
    for (k = 0; k < 6; ++k) mpi_ok(MPI_Sendrecv(&value, 1, MPI_INT, (rank_id + 1) % ranks,
        92, &received, 1, MPI_INT, prev, 92, MPI_COMM_WORLD, MPI_STATUS_IGNORE));
}

int main(int argc, char **argv) {
    int provided, version, minor, integer_bytes, global_errors, host_length;
    char host[MPI_MAX_PROCESSOR_NAME];
    double started;
    const char *test = argc > 1 ? argv[1] : "core";
    mpi_ok(MPI_Init_thread(&argc, &argv, MPI_THREAD_FUNNELED, &provided));
    MPI_Comm_rank(MPI_COMM_WORLD, &rank_id); MPI_Comm_size(MPI_COMM_WORLD, &ranks);
    if (ranks > MAX_RANKS) MPI_Abort(MPI_COMM_WORLD, 91);
    MPI_Get_processor_name(host, &host_length);
    printf("RANK %d %s\n", rank_id, host); fflush(stdout);
    remora_rank_identity(rank_id, ranks);
    MPI_Get_version(&version, &minor); MPI_Type_size(MPI_INT, &integer_bytes);
    check(provided >= MPI_THREAD_FUNNELED && integer_bytes == (int) sizeof(int));
    if (rank_id == 0) printf("META %s %d %d %d %d\n", LANGUAGE, version, minor, integer_bytes, provided);
    started = MPI_Wtime();
    if (!strcmp(test, "core")) core();
    else if (!strcmp(test, "io") && argc > 2) io_case(argv[2]);
    else if (!strcmp(test, "rma")) rma_case();
    else if (!strcmp(test, "errors")) error_case();
    else if (!strcmp(test, "pcontrol")) pcontrol_case();
    else if (!strcmp(test, "abort")) {
        if (rank_id == 0) { puts("ABORT_EXPECTED 17"); fflush(stdout); MPI_Abort(MPI_COMM_WORLD, 17); }
        MPI_Barrier(MPI_COMM_WORLD);
        return 92;
    } else { if (rank_id == 0) fprintf(stderr, "invalid case\n"); MPI_Abort(MPI_COMM_WORLD, 93); }
    mpi_ok(MPI_Allreduce(&errors, &global_errors, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD));
    if (rank_id == 0) {
        printf("RESULT %s %d %d\n", test, ranks, global_errors);
        printf("APP_SECONDS %.17g\n", MPI_Wtime() - started); fflush(stdout);
    }
    mpi_ok(MPI_Finalize());
    return global_errors ? 1 : 0;
}
