/* Unprofiled reference for sites with a broken native F08 Pcontrol entry.
 * This deliberately uses the MPI C binding; it is not a profiler replacement.
 */
#include <mpi.h>
#include <stdio.h>

void remora_pcontrol_reference(int *level)
{
    fprintf(stderr, "REFERENCE_PCONTROL_BINDING_C level=%d\n", *level);
    MPI_Pcontrol(*level);
}
