/* Native scalar F08 lifecycle entries with compiler-probed names.
 * Symbol spelling must be measured from the configured MPI Fortran compiler.
 * No choice-buffer, callback, descriptor or derived-handle ABI is forwarded.
 */
#include <mpi.h>
#include <stddef.h>
#include "symbols.h"

typedef char mpip_f08_integer_abi[(sizeof(MPI_Fint) == sizeof(int)) ? 1 : -1];

void MPIP_F08_INIT(int *ierror)
{
    int result = MPI_Init(NULL, NULL);
    if (ierror != NULL)
        *ierror = result;
}

void MPIP_F08_INIT_THREAD(int *required, int *provided, int *ierror)
{
    int result = MPI_Init_thread(NULL, NULL, *required, provided);
    if (ierror != NULL)
        *ierror = result;
}

void MPIP_F08_FINALIZE(int *ierror)
{
    int result = MPI_Finalize();
    if (ierror != NULL)
        *ierror = result;
}

void MPIP_F08_PCONTROL(int *level)
{
    MPI_Pcontrol(*level);
}
