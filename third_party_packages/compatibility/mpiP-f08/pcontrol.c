/* Scalar F08 Pcontrol bridge. Let the C compiler generate the variadic ABI.
 * Some vendor F08 tail-call stubs omit the AMD64 AL register convention.
 * Keep this object independent of mpiP globals for an unprofiled reference.
 * The MPI symbol is measured from the configured Fortran compiler, not guessed.
 */
#include <mpi.h>
#include "symbols.h"

typedef char mpip_f08_pcontrol_integer_abi[
    (sizeof(MPI_Fint) == sizeof(int)) ? 1 : -1];

void MPIP_F08_PCONTROL(int *level)
{
    (void)MPI_Pcontrol(*level);
}
