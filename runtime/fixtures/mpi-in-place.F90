! Focused independent reproducer: normal versus Fortran MPI_IN_PLACE Allreduce.
program inplace_probe
  use, intrinsic :: iso_c_binding, only: c_int
  use mpi
  implicit none
  integer :: rank, ranks, ierr, provided, normal, inplace, value, expected
  integer :: errors, global_errors, major, minor, bytes, host_length
  character(len=MPI_MAX_PROCESSOR_NAME) :: host
  real(kind=8) :: started
  interface
    subroutine remora_rank_identity(rank, size) bind(C, name="remora_rank_identity")
      import c_int
      integer(c_int), value :: rank, size
    end subroutine
  end interface
  call MPI_Init_thread(MPI_THREAD_FUNNELED,provided,ierr)
  call MPI_Comm_rank(MPI_COMM_WORLD,rank,ierr)
  call MPI_Comm_size(MPI_COMM_WORLD,ranks,ierr)
  call MPI_Get_processor_name(host,host_length,ierr)
  write(*,'(A,1X,I0,1X,A)') 'RANK',rank,trim(host)
  flush(6)
  call remora_rank_identity(int(rank,c_int),int(ranks,c_int))
  call MPI_Get_version(major,minor,ierr)
  call MPI_Type_size(MPI_INTEGER,bytes,ierr)
  if (rank==0) write(*,'(A,4(1X,I0))') 'META fortran',major,minor,bytes,provided
  started=MPI_Wtime()
  value=rank+1
  normal=-99
  call MPI_Allreduce(value,normal,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  inplace=value
  call MPI_Allreduce(MPI_IN_PLACE,inplace,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  expected=ranks*(ranks+1)/2
  write(*,'(A,4(1X,I0))') 'INPLACE_VALUES',rank,normal,inplace,expected
  errors=0
  if (normal/=expected) errors=errors+1
  if (inplace/=expected) errors=errors+1
  call MPI_Allreduce(errors,global_errors,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  if (rank==0) then
    write(*,'(A,2(1X,I0))') 'RESULT inplace',ranks,global_errors
    write(*,'(A,1X,ES24.16)') 'APP_SECONDS',MPI_Wtime()-started
  end if
  call MPI_Finalize(ierr)
  if (global_errors/=0) stop 1
end program
