# mpiP 3.5 compatibility fixes

The public release in `extra/mpiP-3.5.tar.gz` remains byte-for-byte upstream.
`mpiP-source.json` pins the separate patch; the Bash recipe verifies it and
records both patch and patch-tool identity before applying it without fuzz.
The existing modern-C configure probe repair remains separate.

The Fortran helper is compiled by the configured MPI Fortran wrapper. It passes
`mpif.h`'s actual `MPI_IN_PLACE` and `MPI_BOTTOM` addresses to C after MPI
initialization. Generated legacy Fortran wrappers translate choice-buffer
addresses before entering the shared C wrapper. Ordinary buffers and C callers
are unchanged. No vendor symbol names, sentinel integer values or common-block
offsets are assumed. The helper has no Fortran runtime intrinsics. Static and
shared libraries both contain it. This requires the same MPI/Fortran ABI at
build and application runtime, as with the other legacy Fortran wrappers.

The I/O generator now counts `MPI_File_read_at_all` and
`MPI_File_write_at_all` using `count * MPI_Type_size(datatype)`. Conversion to
double precedes multiplication. These are requested payload bytes, consistent
with mpiP 3.5's existing read/write accounting; they are not filesystem traffic
or status-derived actual bytes for short reads/errors. Split, nonblocking and
ordered I/O routines absent from the upstream 3.5 prototype list gain no claim
of coverage. `mpi_f08` interception is a separate qualification.

`tests/test_mpip_compatibility.py` exercises the real patched generator for both
architectures and multiple symbol spellings. Allocated Linux MPI controls must
validate application data and independent byte expectations for each compiler,
MPI, node class, and static/preload mode. Local generator checks alone do not
establish runtime acceptance.
