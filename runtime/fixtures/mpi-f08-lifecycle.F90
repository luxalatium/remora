! F08 lifecycle and optional-ierror regression; single-element choice buffers.
! Focused independent reproducer: normal versus Fortran MPI_IN_PLACE Allreduce.
program inplace_probe
  use, intrinsic :: iso_c_binding, only: c_int, c_loc, c_f_pointer
  use mpi_f08
  implicit none
  integer :: rank, ranks, ierr, provided, normal(1), inplace(1), value(1), expected
  integer :: errors(1), global_errors(1), major, minor, bytes, host_length
  character(len=32) :: init_style
#ifdef REMORA_F08_INPLACE_ARRAY_ALIAS
  integer, pointer :: remora_in_place(:)
#endif
  character(len=MPI_MAX_PROCESSOR_NAME) :: host
  real(kind=8) :: started
  interface
    subroutine remora_rank_identity(rank, size) bind(C, name="remora_rank_identity")
      import c_int
      integer(c_int), value :: rank, size
    end subroutine
  end interface
  call get_environment_variable('REMORA_F08_INIT_STYLE',init_style)
  select case(trim(init_style))
  case('init')
    call MPI_Init()
    call MPI_Query_thread(provided,ierr)
  case('init-present')
    call MPI_Init(ierr)
    call MPI_Query_thread(provided,ierr)
  case default
    call MPI_Init_thread(MPI_THREAD_FUNNELED,provided)
  end select
  call MPI_Comm_rank(MPI_COMM_WORLD,rank,ierr)
  call MPI_Comm_size(MPI_COMM_WORLD,ranks,ierr)
  call MPI_Get_processor_name(host,host_length,ierr)
  write(*,'(A,1X,I0,1X,A)') 'RANK',rank,trim(host)
  flush(6)
  call remora_rank_identity(int(rank,c_int),int(ranks,c_int))
  call MPI_Get_version(major,minor,ierr)
  call MPI_Type_size(MPI_INTEGER,bytes,ierr)
  if (rank==0) write(*,'(A,4(1X,I0))') 'META fortran-f08',major,minor,bytes,provided
  started=MPI_Wtime()
  value=rank+1
  normal=-99
  call MPI_Allreduce(value,normal,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  inplace=value
#ifdef REMORA_F08_INPLACE_ARRAY_ALIAS
  call c_f_pointer(c_loc(MPI_IN_PLACE),remora_in_place,[1])
  call MPI_Allreduce(remora_in_place,inplace,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
#else
  call MPI_Allreduce(MPI_IN_PLACE,inplace,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
#endif
  expected=ranks*(ranks+1)/2
  write(*,'(A,4(1X,I0))') 'INPLACE_VALUES',rank,normal,inplace,expected
  errors=0
  if (any(normal/=expected)) errors=errors+1
  if (any(inplace/=expected)) errors=errors+1
  call MPI_Allreduce(errors,global_errors,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  if (rank==0) then
    write(*,'(A,2(1X,I0))') 'RESULT inplace',ranks,global_errors
    write(*,'(A,1X,ES24.16)') 'APP_SECONDS',MPI_Wtime()-started
  end if
  call MPI_Finalize()
  if (any(global_errors/=0)) stop 1
end program
