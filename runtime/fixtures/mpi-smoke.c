#include <mpi.h>
#include <stdio.h>

int main(int argc, char **argv)
{
    int rank, size, result = 0;
    MPI_Init(&argc, &argv);
    MPI_Comm_rank(MPI_COMM_WORLD, &rank);
    MPI_Comm_size(MPI_COMM_WORLD, &size);
    for (int i = 0; i < 8; ++i) {
        MPI_Allreduce(&rank, &result, 1, MPI_INT, MPI_SUM, MPI_COMM_WORLD);
        MPI_Barrier(MPI_COMM_WORLD);
    }
    int valid = size == 2 && result == 1;
    printf("rank=%d size=%d sum=%d valid=%d\n", rank, size, result, valid);
    MPI_Finalize();
    return valid ? 0 : 1;
}
