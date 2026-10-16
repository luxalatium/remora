program mpi_comprehensive
  use, intrinsic :: iso_c_binding, only: c_int
#ifdef USE_F08
  use mpi_f08
#else
  use mpi
#endif
  implicit none
  integer, parameter :: loops=4, max_ranks=64
  integer :: rank_id, ranks, errors=0, global_errors, provided, ierr, version, minor
  integer :: integer_bytes, host_length, i, k, next, prev, peer, subrank, subsize, error_class, error_code
  integer, target, asynchronous :: exposed
  integer, asynchronous :: sendbuf(64), recvbuf(64), value, total, outgoing
  integer :: gathered(max_ranks), exchanged(max_ranks)
  integer(kind=MPI_OFFSET_KIND) :: offset
  integer(kind=MPI_ADDRESS_KIND) :: window_bytes, displacement
  real(kind=8) :: started
  character(len=MPI_MAX_PROCESSOR_NAME) :: host
  character(len=32) :: test
  character(len=2048) :: path
#ifdef USE_F08
  type(MPI_Comm) :: sub
  type(MPI_Datatype) :: stride
  type(MPI_Request) :: requests(2), request
  type(MPI_File) :: file
  type(MPI_Win) :: window
#else
  integer :: sub, stride, requests(2), request, file, window
#endif
  interface
    subroutine remora_rank_identity(rank, size) bind(C, name="remora_rank_identity")
      import c_int
      integer(c_int), value :: rank, size
    end subroutine
  end interface

  test='core'
  if (command_argument_count() > 0) call get_command_argument(1,test)
  call MPI_Init_thread(MPI_THREAD_FUNNELED, provided, ierr)
  call ok(ierr)
  call MPI_Comm_rank(MPI_COMM_WORLD, rank_id, ierr)
  call MPI_Comm_size(MPI_COMM_WORLD, ranks, ierr)
  if (ranks > max_ranks) call MPI_Abort(MPI_COMM_WORLD,91,ierr)
  call MPI_Get_processor_name(host, host_length, ierr)
  write(*,'(A,1X,I0,1X,A)') 'RANK',rank_id,trim(host)
  flush(6)
  call remora_rank_identity(int(rank_id,c_int),int(ranks,c_int))
  call MPI_Get_version(version,minor,ierr)
  call MPI_Type_size(MPI_INTEGER,integer_bytes,ierr)
  call check(provided >= MPI_THREAD_FUNNELED .and. integer_bytes == storage_size(value)/8)
  if (rank_id==0) then
#ifdef USE_F08
    write(*,'(A,4(1X,I0))') 'META fortran-f08',version,minor,integer_bytes,provided
#else
    write(*,'(A,4(1X,I0))') 'META fortran',version,minor,integer_bytes,provided
#endif
  end if
  started=MPI_Wtime()
  next=mod(rank_id+1,ranks)
  prev=mod(rank_id+ranks-1,ranks)
  select case(trim(test))
  case('core')
    call MPI_Barrier(MPI_COMM_WORLD,ierr)
    call ok(ierr)
    do k=0,loops-1
      do i=1,64
        sendbuf(i)=rank_id*100+i-1+k*1000
        recvbuf(i)=-9
      end do
      peer=ieor(rank_id,1)
      if (peer < ranks) then
        if (mod(rank_id,2)==0) then
          call MPI_Send(sendbuf,4,MPI_INTEGER,peer,10,MPI_COMM_WORLD,ierr)
          call ok(ierr)
          call MPI_Recv(recvbuf,4,MPI_INTEGER,peer,11,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
          call ok(ierr)
        else
          call MPI_Recv(recvbuf,4,MPI_INTEGER,peer,10,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
          call ok(ierr)
          call MPI_Send(sendbuf,4,MPI_INTEGER,peer,11,MPI_COMM_WORLD,ierr)
          call ok(ierr)
        end if
        do i=1,4
          call check(recvbuf(i)==peer*100+i-1+k*1000)
        end do
      end if
      call MPI_Sendrecv(sendbuf,8,MPI_INTEGER,next,20,recvbuf,8,MPI_INTEGER, &
                        prev,20,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      do i=1,8
        call check(recvbuf(i)==prev*100+i-1+k*1000)
      end do
      call MPI_Sendrecv(sendbuf,0,MPI_INTEGER,next,21,recvbuf,0,MPI_INTEGER, &
                        prev,21,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      call MPI_Irecv(recvbuf,64,MPI_INTEGER,prev,30,MPI_COMM_WORLD,requests(1),ierr)
      call ok(ierr)
      call MPI_Isend(sendbuf,64,MPI_INTEGER,next,30,MPI_COMM_WORLD,requests(2),ierr)
      call ok(ierr)
      call MPI_Waitall(2,requests,MPI_STATUSES_IGNORE,ierr)
      call ok(ierr)
      do i=1,64
        call check(recvbuf(i)==prev*100+i-1+k*1000)
      end do
      value=-1
      if (rank_id==0) value=100+k
      call MPI_Bcast(value,1,MPI_INTEGER,0,MPI_COMM_WORLD,ierr)
      call ok(ierr)
      call check(value==100+k)
      value=rank_id+1
      total=value
      if (mod(k,2)==0) then
        call MPI_Allreduce(MPI_IN_PLACE,total,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
      else
        call MPI_Allreduce(value,total,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
      end if
      call ok(ierr)
      call check(total==ranks*(ranks+1)/2)
      call MPI_Allgather(rank_id,1,MPI_INTEGER,gathered,1,MPI_INTEGER,MPI_COMM_WORLD,ierr)
      call ok(ierr)
      do i=1,ranks
        call check(gathered(i)==i-1)
        gathered(i)=rank_id*100+i-1
      end do
      call MPI_Alltoall(gathered,1,MPI_INTEGER,exchanged,1,MPI_INTEGER,MPI_COMM_WORLD,ierr)
      call ok(ierr)
      do i=1,ranks
        call check(exchanged(i)==(i-1)*100+rank_id)
      end do
      call MPI_Iallreduce(value,total,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,request,ierr)
      call ok(ierr)
      call MPI_Wait(request,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      call check(total==ranks*(ranks+1)/2)
    end do
    call MPI_Comm_split(MPI_COMM_WORLD,mod(rank_id,2),rank_id,sub,ierr)
    call ok(ierr)
    call MPI_Comm_rank(sub,subrank,ierr)
    call MPI_Comm_size(sub,subsize,ierr)
    do k=1,loops
      value=subrank+1
      call MPI_Allreduce(value,total,1,MPI_INTEGER,MPI_SUM,sub,ierr)
      call ok(ierr)
      call check(total==subsize*(subsize+1)/2)
    end do
    call MPI_Comm_free(sub,ierr)
    call ok(ierr)
    call MPI_Type_vector(4,1,2,MPI_INTEGER,stride,ierr)
    call ok(ierr)
    call MPI_Type_commit(stride,ierr)
    call ok(ierr)
    do k=1,loops
      do i=1,8
        sendbuf(i)=rank_id*100+i-1
        recvbuf(i)=-9
      end do
      call MPI_Sendrecv(sendbuf,1,stride,next,40,recvbuf,1,stride, &
                        prev,40,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      do i=1,8
        if (mod(i,2)==1) then
          call check(recvbuf(i)==prev*100+i-1)
        else
          call check(recvbuf(i)==-9)
        end if
      end do
    end do
    call MPI_Type_free(stride,ierr)
    call ok(ierr)
  case('io')
    call get_command_argument(2,path)
    call MPI_File_open(MPI_COMM_WORLD,trim(path),ior(MPI_MODE_CREATE,MPI_MODE_RDWR),MPI_INFO_NULL,file,ierr)
    call ok(ierr)
    call MPI_File_set_size(file,0_MPI_OFFSET_KIND,ierr)
    call ok(ierr)
    do k=0,loops-1
      do i=1,16
        sendbuf(i)=rank_id*100+i-1+k*1000
      end do
      offset=int(rank_id,MPI_OFFSET_KIND)*16*integer_bytes
      call MPI_File_write_at_all(file,offset,sendbuf,16,MPI_INTEGER,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      call MPI_File_sync(file,ierr)
      call ok(ierr)
      call MPI_File_read_at_all(file,offset,recvbuf,16,MPI_INTEGER,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      do i=1,16
        call check(recvbuf(i)==sendbuf(i))
      end do
    end do
    call MPI_File_close(file,ierr)
    call ok(ierr)
  case('rma')
    exposed=-1
    window_bytes=int(integer_bytes,MPI_ADDRESS_KIND)
    displacement=0_MPI_ADDRESS_KIND
    call MPI_Win_create(exposed,window_bytes,integer_bytes,MPI_INFO_NULL,MPI_COMM_WORLD,window,ierr)
    call ok(ierr)
    do k=0,loops-1
      outgoing=rank_id+1000*k
      call MPI_Win_fence(0,window,ierr)
      call ok(ierr)
      call MPI_Put(outgoing,1,MPI_INTEGER,next,displacement,1,MPI_INTEGER,window,ierr)
      call ok(ierr)
      call MPI_Win_fence(0,window,ierr)
      call ok(ierr)
      call check(exposed==prev+1000*k)
    end do
    call MPI_Win_free(window,ierr)
    call ok(ierr)
  case('errors')
    call MPI_Comm_set_errhandler(MPI_COMM_WORLD,MPI_ERRORS_RETURN,ierr)
    call ok(ierr)
    value=1
    call MPI_Send(value,0,MPI_INTEGER,ranks,80,MPI_COMM_WORLD,ierr)
    call check(ierr/=MPI_SUCCESS)
    error_code=ierr
    call MPI_Error_class(error_code,error_class,ierr)
    call ok(ierr)
    call check(error_class==MPI_ERR_RANK)
  case('pcontrol')
    value=rank_id
    call MPI_Pcontrol(0)
    do k=1,2
      call MPI_Sendrecv(value,1,MPI_INTEGER,next,90,total,1,MPI_INTEGER,prev,90,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
    end do
    call MPI_Pcontrol(2)
    call MPI_Pcontrol(1)
    do k=1,loops
      call MPI_Sendrecv(value,1,MPI_INTEGER,next,91,total,1,MPI_INTEGER,prev,91,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
      call check(total==prev)
    end do
    call MPI_Pcontrol(3)
    call MPI_Pcontrol(0)
    do k=1,6
      call MPI_Sendrecv(value,1,MPI_INTEGER,next,92,total,1,MPI_INTEGER,prev,92,MPI_COMM_WORLD,MPI_STATUS_IGNORE,ierr)
      call ok(ierr)
    end do
  case('abort')
    call MPI_Barrier(MPI_COMM_WORLD,ierr)
    call ok(ierr)
    if (rank_id==0) then
      write(*,'(A)') 'ABORT_EXPECTED 17'
      flush(6)
      call MPI_Abort(MPI_COMM_WORLD,17,ierr)
    end if
    call MPI_Barrier(MPI_COMM_WORLD,ierr)
    stop 92
  case default
    call MPI_Abort(MPI_COMM_WORLD,93,ierr)
  end select
  call MPI_Allreduce(errors,global_errors,1,MPI_INTEGER,MPI_SUM,MPI_COMM_WORLD,ierr)
  call ok(ierr)
  if (rank_id==0) then
    write(*,'(A,1X,A,2(1X,I0))') 'RESULT',trim(test),ranks,global_errors
    write(*,'(A,1X,ES24.16)') 'APP_SECONDS',MPI_Wtime()-started
    flush(6)
  end if
  call MPI_Finalize(ierr)
  if (global_errors/=0) stop 1
contains
  subroutine check(condition)
    logical, intent(in) :: condition
    if (.not.condition) errors=errors+1
  end subroutine
  subroutine ok(rc)
    integer, intent(in) :: rc
    integer :: abort_ierr
    if (rc/=MPI_SUCCESS) then
      write(*,*) 'UNEXPECTED_MPI_ERROR rank=',rank_id,' rc=',rc
      call MPI_Abort(MPI_COMM_WORLD,90,abort_ierr)
    end if
  end subroutine
end program
