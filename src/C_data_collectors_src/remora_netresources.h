/* MIT. Optional network snapshots. Contract: docs/network-resources.md. */
#ifndef REMORA_NETRESOURCES_H
#define REMORA_NETRESOURCES_H
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/utsname.h>
#include <fcntl.h>
#include <unistd.h>
#include <dirent.h>
#include <errno.h>
#include <ctype.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#define NX_PATH 1024
#define NX_ENT 128
struct nx_field { const char *name, *unit; unsigned scale; };
struct nx_entity { char name[128], base[NX_PATH], driver[128]; int fabric, hfi, mlx; };
static struct nx_entity nx_entities[NX_ENT];
static unsigned nx_count, nx_records, nx_readable;
static int nx_bad, nx_first;
static const char *nx_limit;
static const char *nx_root, *nx_module;
static FILE *nx_out;
static const struct nx_field nx_errors[]={
 {"rx_errors","events",1},{"tx_errors","events",1},{"rx_dropped","packets",1},
 {"tx_dropped","packets",1},{"rx_length_errors","events",1},{"rx_over_errors","events",1},
 {"rx_crc_errors","events",1},{"rx_frame_errors","events",1},{"rx_fifo_errors","events",1},
 {"rx_missed_errors","events",1},{"tx_aborted_errors","events",1},{"tx_carrier_errors","events",1},
 {"tx_fifo_errors","events",1},{"tx_heartbeat_errors","events",1},{"tx_window_errors","events",1},
 {"collisions","events",1},{"rx_nohandler","packets",1},{"rx_otherhost_dropped","packets",1}};
static const struct nx_field nx_ib[]={
 {"symbol_error","events",1},{"link_downed","events",1},{"link_error_recovery","events",1},
 {"port_xmit_discards","packets",1},{"port_rcv_errors","events",1},
 {"port_rcv_constraint_errors","events",1},{"port_xmit_constraint_errors","events",1},
 {"local_link_integrity_errors","events",1},{"excessive_buffer_overrun_errors","events",1},
 {"VL15_dropped","packets",1},{"port_xmit_wait","native_ticks",1},
 {"unicast_xmit_packets","packets",1},{"unicast_rcv_packets","packets",1},
 {"multicast_xmit_packets","packets",1},{"multicast_rcv_packets","packets",1}};
static const struct nx_field nx_mlx[]={
 {"out_of_buffer","native_count",1},{"duplicate_request","native_count",1},
 {"local_ack_timeout_err","native_count",1},{"packet_seq_err","native_count",1},
 {"rnr_nak_retry_err","native_count",1},{"req_cqe_error","native_count",1},
 {"resp_cqe_error","native_count",1},{"req_cqe_flush_error","native_count",1},
 {"resp_cqe_flush_error","native_count",1},{"req_remote_access_errors","native_count",1},
 {"req_remote_invalid_request","native_count",1},{"resp_local_length_error","native_count",1},
 {"resp_remote_access_errors","native_count",1},{"rx_read_requests","requests",1},
 {"rx_write_requests","requests",1},{"rx_atomic_requests","requests",1},
 {"np_cnp_sent","packets",1},{"rp_cnp_handled","packets",1},
 {"rp_cnp_ignored","packets",1},{"np_ecn_marked_roce_packets","packets",1}};
/* Only known cumulative fields; gauges/high-water/per-VL unknowns are excluded. */
static const struct nx_field nx_opa[]={
 {"TxPkt","packets",1},{"RxPkt","packets",1},{"TxWords","DWORD",4},{"RxWords","DWORD",4},
 {"TxWait","native_ticks",1},{"DmaWait","native_ticks",1},
 {"RcvCstrErr","native_count",1},{"XmitCstrErr","native_count",1},
 {"UnknownFrame","native_count",1},{"PktDrop","native_count",1},
 {"Unaligned","native_count",1},{"SeqNak","native_count",1},
 {"RnrNak","native_count",1},{"OtherNak","native_count",1},
 {"RcTimeOut","native_count",1},{"RcResend","native_count",1},
 {"RcSeqNak","native_count",1}};
static int nx_fmt(char *s,size_t n,const char *format,...) {
 va_list a;va_start(a,format);int r=vsnprintf(s,n,format,a);va_end(a);
 if(r<0 || (size_t)r>=n) {errno=ENAMETOOLONG;return -1;}return 0;
}
static int nx_name(const char *s) {
 if(!s || !*s || strlen(s)>120 || !strcmp(s,".") || !strcmp(s,"..")) return 0;
 for(;*s;s++) if(!isalnum((unsigned char)*s) && !strchr("_.:-",*s)) return 0;
 return 1;
}
static void nx_quote(const char *s) {
 if(!s) {fputs("null",nx_out);return;}fputc('"',nx_out);
 for(const unsigned char *p=(const unsigned char*)s;*p;p++) {
  if(*p=='"' || *p=='\\') {fputc('\\',nx_out);fputc(*p,nx_out);}
  else if(*p<32 || *p>=127) fprintf(nx_out,"\\u%04x",*p);
  else fputc(*p,nx_out);
 }fputc('"',nx_out);
}
static int nx_text(const char *path,char *s,size_t cap,int link) {
 char full[NX_PATH*2];if(nx_fmt(full,sizeof full,"%s/%s",nx_root,path)) return -1;
 if(link) {ssize_t n=readlink(full,s,cap-1);if(n<0) return -1;if((size_t)n>=cap-1) {errno=EOVERFLOW;return -1;}s[n]=0;return 0;}
 int fd=open(full,O_RDONLY|O_NONBLOCK);if(fd<0) return -1;
 struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode)) {close(fd);errno=EBADMSG;return -1;}
 size_t used=0;int saved=0;
 while(used<cap-1) {ssize_t n=read(fd,s+used,cap-1-used);if(n<0) {if(errno==EINTR) continue;saved=errno;break;}if(!n) break;used+=(size_t)n;}
 if(used==cap-1) saved=EOVERFLOW;
 if(close(fd) && !saved) saved=errno;
 if(!saved && memchr(s,0,used)) saved=EBADMSG;
 if(saved) {errno=saved;return -1;}s[used]=0;return 0;
}
static void nx_trim(char *s) {size_t n=strlen(s);while(n && isspace((unsigned char)s[n-1])) s[--n]=0;}
static int nx_uint(const char *s,uint64_t *v) {
 if(!*s || !isdigit((unsigned char)*s)) return -1;
 uint64_t x=0;for(;*s;s++) {unsigned d=(unsigned)(*s-'0');if(d>9 || x>(UINT64_MAX-d)/10) return -1;x=x*10+d;}
 *v=x;return 0;
}
static const char *nx_error(int e) {
 if(e==ENOENT || e==ENODEV) return "ABSENT";
 if(e==EACCES || e==EPERM) return "DENIED";
 if(e==EINVAL || e==ENOSYS || e==EOPNOTSUPP) return "UNSUPPORTED";
 if(e==EBADMSG || e==ERANGE) return "MALFORMED";
 return "ERROR";
}
/* Every attempt is evidence, including unsupported candidates before fallback. */
static int nx_record(struct nx_entity *e,const char *metric,const char *path,
                     const char *kind,const char *unit,unsigned scale,int link,int primary) {
 long position=ftell(nx_out);
 if(position<0 || position>8*1024*1024-65536 || nx_records>=8192) {
  nx_bad=1;nx_limit="sample_record_or_byte_limit";return EOVERFLOW;
 }
 char raw[2048]={0},value[2048]={0};uint64_t number;
 int r=nx_text(path,raw,sizeof raw,link),error=r?errno:0;
 if(!r) {memcpy(value,raw,strlen(raw)+1);nx_trim(value);
  if(!strcmp(kind,"counter") && nx_uint(value,&number)) {r=-1;error=EBADMSG;}}
 if(!nx_first) fputc(',',nx_out);
 nx_first=0;nx_records++;
 fputs("{\"entity\":",nx_out);nx_quote(e->name);
 fputs(",\"scope\":",nx_out);nx_quote(e->fabric?"rdma_port":"netdev");
 fputs(",\"metric\":",nx_out);nx_quote(metric);fputs(",\"source\":",nx_out);nx_quote(path);
 fputs(",\"kind\":",nx_out);nx_quote(kind);fputs(",\"unit\":",nx_out);nx_quote(unit);
 fprintf(nx_out,",\"byte_scale\":%u,\"status\":",scale);nx_quote(r?nx_error(error):"OK");
 fprintf(nx_out,",\"errno\":%d,\"raw\":",error);nx_quote(error && !*raw?NULL:raw);
 fputs(",\"value\":",nx_out);nx_quote(r?NULL:value);fputc('}',nx_out);
 if(!r && primary) nx_readable++;
 if(r && error!=ENOENT && error!=ENODEV && error!=EINVAL && error!=ENOSYS && error!=EOPNOTSUPP) nx_bad=1;
 return r?error:0;
}
static void nx_field(struct nx_entity *e,const char *key,const char *relative,
                     const char *kind,const char *unit,int link,int primary) {
 char path[NX_PATH];if(nx_fmt(path,sizeof path,"%s/%s",e->base,relative)) {nx_bad=1;return;}
 nx_record(e,key,path,kind,unit,0,link,primary);
}
static int nx_family(struct nx_entity *e) {
 char dev[128],path[NX_PATH],target[NX_PATH];const char *slash=strchr(e->name,'/');
 size_t n=(size_t)(slash-e->name);memcpy(dev,e->name,n);dev[n]=0;
 if(nx_fmt(path,sizeof path,"class/infiniband/%s/device/driver",dev)) return -1;
 if(!nx_text(path,target,sizeof target,1)) {
  const char *b=strrchr(target,'/');b=b?b+1:target;
  if(nx_fmt(e->driver,sizeof e->driver,"%s",b)) return -1;
  e->hfi=!strcmp(b,"hfi1");e->mlx=!strcmp(b,"mlx5_core") || !strcmp(b,"mlx4_core");
 } else {
  if(errno!=ENOENT && errno!=ENODEV) return -1;
  e->hfi=!strncmp(dev,"hfi1_",5);e->mlx=!strncmp(dev,"mlx5_",5) || !strncmp(dev,"mlx4_",5);
  strcpy(e->driver,"name_fallback");
 }return 0;
}
static int nx_add(const char *name,int fabric,int explicit_selection) {
 if(nx_count>=NX_ENT) return 65;
 struct nx_entity e;memset(&e,0,sizeof e);e.fabric=fabric;
 if(nx_fmt(e.name,sizeof e.name,"%s",name)) return 65;
 if(fabric) {
  const char *slash=strchr(name,'/');char dev[128];
  if(!slash || slash==name || strchr(slash+1,'/')) return 65;
  size_t n=(size_t)(slash-name);memcpy(dev,name,n);dev[n]=0;
  if(!nx_name(dev) || !nx_name(slash+1)) return 65;
  for(const char *p=slash+1;*p;p++) if(!isdigit((unsigned char)*p)) return 65;
  if(nx_fmt(e.base,sizeof e.base,"class/infiniband/%s/ports/%s",dev,slash+1) || nx_family(&e)) return 74;
 } else {
  if(!nx_name(name) || nx_fmt(e.base,sizeof e.base,"class/net/%s",name)) return 65;
 }
 char full[NX_PATH*2];struct stat st;
 if(nx_fmt(full,sizeof full,"%s/%s",nx_root,e.base) || stat(full,&st)) return explicit_selection?69:74;
 if(!S_ISDIR(st.st_mode)) return 74;
 if(!strcmp(nx_module,"neterrors") && !explicit_selection) {
  char p[NX_PATH],t[64];nx_fmt(p,sizeof p,"%s/type",e.base);
  if(nx_text(p,t,sizeof t,0)) return 74;
  nx_trim(t);if(strcmp(t,"1")) return 0;
 }
 if(fabric) {
  int match=strcmp(nx_module,"mlxcounters") || e.mlx;
  match=match && (strcmp(nx_module,"opacounters") || e.hfi);
  if(!strcmp(nx_module,"ibcounters")) {
   char p[NX_PATH],t[64];nx_fmt(p,sizeof p,"%s/link_layer",e.base);
   if(nx_text(p,t,sizeof t,0)) return 74;
  nx_trim(t);match=match && !strcmp(t,"InfiniBand");
  }
  if(!match) return explicit_selection?69:0;
 }
 for(unsigned i=0;i<nx_count;i++) if(nx_entities[i].fabric==fabric && !strcmp(nx_entities[i].name,name)) return 65;
 nx_entities[nx_count++]=e;return 0;
}
static int nx_cmp(const void *a,const void *b) {
 const struct nx_entity *x=a,*y=b;return x->fabric!=y->fabric?x->fabric-y->fabric:strcmp(x->name,y->name);
}
static int nx_discover(int fabric) {
 const char *selection=getenv(!fabric?"REMORA_ETH_INTERFACES":!strcmp(nx_module,"opacounters")?"REMORA_OPA_PORTS":"REMORA_IB_PORTS");
 if(selection) {
  size_t len=strlen(selection);if(!len || len>8192 || selection[0]==',' || selection[len-1]==',' || strstr(selection,",,")) return 65;
  char *copy=strdup(selection),*save=NULL;if(!copy) return 74;int status=0;
  for(char *s=strtok_r(copy,",",&save);s;s=strtok_r(NULL,",",&save)) {status=nx_add(s,fabric,1);if(status) break;}
  free(copy);return status;
 }
 char path[NX_PATH*2];nx_fmt(path,sizeof path,"%s/class/%s",nx_root,fabric?"infiniband":"net");
 DIR *d=opendir(path);if(!d) return errno==ENOENT?0:74;
 int status=0;unsigned visited=0;struct dirent *de;
 while((de=readdir(d))) {
  if(de->d_name[0]=='.') continue;
  if(++visited>256 || !nx_name(de->d_name)) {status=65;break;}
  if(!fabric) {status=nx_add(de->d_name,0,0);if(status) break;continue;}
  char ports[NX_PATH*2];if(nx_fmt(ports,sizeof ports,"%s/%s/ports",path,de->d_name)) {status=65;break;}
  DIR *pd=opendir(ports);if(!pd) {status=74;break;}struct dirent *pe;
  while((pe=readdir(pd))) {
   if(pe->d_name[0]=='.') continue;
   char name[128];if(++visited>384 || nx_fmt(name,sizeof name,"%s/%s",de->d_name,pe->d_name)) {status=65;break;}
   status=nx_add(name,1,0);if(status) break;
  }closedir(pd);if(status) break;
 }closedir(d);return status;
}
static void nx_metadata(struct nx_entity *e,int primary) {
 if(!e->fabric) {
  const char *keys[]={"ifindex","iflink","type","flags","operstate","carrier","mtu","speed","duplex","address","addr_assign_type"};
  const char *units[]={"index","index","ARPHRD","bitmask","text","boolean","bytes","Mbit/s","text","text","enum"};
  for(unsigned i=0;i<sizeof keys/sizeof *keys;i++) nx_field(e,keys[i],keys[i],"metadata",units[i],0,primary);
 } else {
  const char *keys[]={"state","phys_state","link_layer","rate","lid","sm_lid","cap_mask","active_width","active_speed"};
  for(unsigned i=0;i<sizeof keys/sizeof *keys;i++) nx_field(e,keys[i],keys[i],"metadata","native_text",0,primary);
  char devbase[NX_PATH];nx_fmt(devbase,sizeof devbase,"class/infiniband/%.*s",(int)(strchr(e->name,'/')-e->name),e->name);
  const char *keys2[]={"node_guid","sys_image_guid","fw_ver","node_type","hca_type","board_id"};
  for(unsigned i=0;i<sizeof keys2/sizeof *keys2;i++) {char path[NX_PATH];nx_fmt(path,sizeof path,"%s/%s",devbase,keys2[i]);nx_record(e,keys2[i],path,"metadata","native_text",0,0,primary);}
  if(primary) {
   char dir[NX_PATH*2],rel[NX_PATH];nx_fmt(rel,sizeof rel,"%s/gid_attrs/ndevs",e->base);nx_fmt(dir,sizeof dir,"%s/%s",nx_root,rel);
   DIR *d=opendir(dir);
   if(d) {struct dirent *de;unsigned seen=0;
    while((de=readdir(d))) {if(de->d_name[0]=='.') continue;if(++seen>512 || !nx_name(de->d_name)) {nx_bad=1;nx_limit="gid_mapping_limit_or_invalid_name";break;}
     char path[NX_PATH],metric[180];if(nx_fmt(path,sizeof path,"%s/%s",rel,de->d_name) || nx_fmt(metric,sizeof metric,"gid_netdev.%s",de->d_name)) {nx_bad=1;break;}
     nx_record(e,metric,path,"metadata","netdev_name",0,0,1);
    }closedir(d);
   } else nx_record(e,"gid_netdev",rel,"metadata","netdev_name",0,0,1);
  }
 }
 char device[NX_PATH];
 if(e->fabric) nx_fmt(device,sizeof device,"class/infiniband/%.*s/device",(int)(strchr(e->name,'/')-e->name),e->name);
 else nx_fmt(device,sizeof device,"%s/device",e->base);
 const char *devkeys[]={"numa_node","local_cpulist","current_link_speed","current_link_width","vendor","device","driver"};
 for(unsigned i=0;i<sizeof devkeys/sizeof *devkeys;i++) {
  char path[NX_PATH];nx_fmt(path,sizeof path,"%s/%s",device,devkeys[i]);nx_record(e,devkeys[i],path,"metadata","native_text",0,i==6,primary);
 }
 nx_record(e,"device_path",device,"metadata","symlink_target",0,1,primary);
}
static void nx_counters(struct nx_entity *e) {
 const struct nx_field *fields;size_t count;const char *dir;
 if(!strcmp(nx_module,"neterrors")) {fields=nx_errors;count=sizeof nx_errors/sizeof *fields;dir="statistics";}
 else if(!strcmp(nx_module,"ibcounters")) {fields=nx_ib;count=sizeof nx_ib/sizeof *fields;dir="counters";}
 else if(!strcmp(nx_module,"mlxcounters")) {fields=nx_mlx;count=sizeof nx_mlx/sizeof *fields;dir="hw_counters";}
 else {fields=nx_opa;count=sizeof nx_opa/sizeof *fields;dir="hw_counters";}
 for(size_t i=0;i<count;i++) {
  char path[NX_PATH];const struct nx_field *f=&fields[i];
  if(!strcmp(nx_module,"ibcounters")) {
   nx_fmt(path,sizeof path,"%s/counters_ext/%s_64",e->base,f->name);
   int error=nx_record(e,f->name,path,"counter",f->unit,0,0,1);
   if(!error || (error!=ENOENT && error!=ENODEV && error!=EINVAL && error!=ENOSYS && error!=EOPNOTSUPP)) continue;
  }
  nx_fmt(path,sizeof path,"%s/%s/%s",e->base,dir,f->name);
  nx_record(e,f->name,path,"counter",f->unit,!strcmp(f->unit,"DWORD")?f->scale:0,0,1);
 }
}
static int nx_open(int dir,const char *name,int flags) {
 int fd=openat(dir,name,flags|O_NOFOLLOW|O_NONBLOCK,0600);if(fd<0) return -1;
 struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || st.st_nlink!=1) {close(fd);errno=EPERM;return -1;}return fd;
}
static void nx_time(const char *key,clockid_t clock) {
 struct timespec t;if(clock_gettime(clock,&t)) {nx_bad=1;t.tv_sec=0;t.tv_nsec=0;}
 fprintf(nx_out,"\"%s\":\"%" PRId64 ".%09ld\"",key,(int64_t)t.tv_sec,t.tv_nsec);
}
static int remora_netresources_main(const char *module,int argc,char **argv) {
 if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--check")) || !nx_name(argv[1])) return 65;
 nx_module=module;nx_root=getenv("REMORA_NETWORK_SYSFS_ROOT");if(!nx_root) nx_root="/sys";
 if(nx_root[0]!='/' || strlen(nx_root)>700 || strpbrk(nx_root,"\r\n\t")) return 65;
 int info=!strcmp(module,"netinfo"),net=!strcmp(module,"neterrors"),status=0;
 if(info || net) status=nx_discover(0);
 if(!status && (info || !net)) status=nx_discover(1);
 if(status) return status;
 if(!nx_count) return 69;
 qsort(nx_entities,nx_count,sizeof *nx_entities,nx_cmp);
 if(argc==5) return 0;
 int dir=open(argv[3],O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0) return 74;
 char filename[256],lock[280],pending[280];nx_fmt(filename,sizeof filename,"%s_%s.jsonl",module,argv[1]);nx_fmt(lock,sizeof lock,"%s.lock",filename);nx_fmt(pending,sizeof pending,"%s.pending",filename);
 int lfd=nx_open(dir,lock,O_RDWR|O_CREAT);if(lfd<0) {close(dir);return 74;}
 if(flock(lfd,LOCK_EX|LOCK_NB)) {close(lfd);close(dir);return 75;}
 int pfd=nx_open(dir,pending,O_WRONLY|O_CREAT|O_EXCL),fd=-1;
 char *buffer=NULL;size_t size=0;
 if(pfd<0) goto done;
 fd=nx_open(dir,filename,O_RDWR|O_APPEND|O_CREAT);if(fd<0) goto done;
 struct stat st;if(fstat(fd,&st)) goto done;
 if(st.st_size) {char last;if(pread(fd,&last,1,st.st_size-1)!=1 || last!='\n') goto done;}
 nx_out=open_memstream(&buffer,&size);if(!nx_out) goto done;
 fputs("{\"schema\":\"remora-network-resources/v1\",\"module\":",nx_out);nx_quote(module);
 fputs(",\"node\":",nx_out);nx_quote(argv[1]);fputs(",\"sysfs_root\":",nx_out);nx_quote(nx_root);
 struct utsname kernel;fputs(",\"kernel\":",nx_out);nx_quote(uname(&kernel)?"UNKNOWN":kernel.release);
 char ns[256];ssize_t n=readlink("/proc/self/ns/net",ns,sizeof ns-1);if(n>=0) ns[n]=0;
 fputs(",\"network_namespace\":",nx_out);nx_quote(n<0?"UNKNOWN":ns);
 char boot[128]={0};int bfd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_NONBLOCK);
 if(bfd>=0) {n=read(bfd,boot,sizeof boot-1);if(n>0) boot[n]=0;close(bfd);nx_trim(boot);}
 fputs(",\"boot_id\":",nx_out);nx_quote(*boot?boot:"UNKNOWN");fputc(',',nx_out);nx_time("wall_start",CLOCK_REALTIME);fputc(',',nx_out);nx_time("mono_start",CLOCK_MONOTONIC);
 fputs(",\"entities\":[",nx_out);
 for(unsigned i=0;i<nx_count;i++) {struct nx_entity *e=&nx_entities[i];if(i) fputc(',',nx_out);fputs("{\"name\":",nx_out);nx_quote(e->name);fputs(",\"family_evidence\":",nx_out);nx_quote(e->fabric?e->driver:"netdev");fputc('}',nx_out);}
 fputs("],\"records\":[",nx_out);nx_first=1;
 for(unsigned i=0;i<nx_count;i++) {nx_metadata(&nx_entities[i],info);if(!info) nx_counters(&nx_entities[i]);}
 fputs("],",nx_out);nx_time("wall_end",CLOCK_REALTIME);fputc(',',nx_out);nx_time("mono_end",CLOCK_MONOTONIC);
 fputs(",\"limitation\":",nx_out);nx_quote(nx_limit);
 fprintf(nx_out,",\"status\":\"%s\",\"readable_primary\":%u,\"record_count\":%u}\n",nx_bad?"PARTIAL":nx_readable?"OK":"UNSUPPORTED",nx_readable,nx_records);
 int bad=ferror(nx_out);if(fclose(nx_out)) bad=1;nx_out=NULL;
 if(bad || size>8*1024*1024) goto done;
 size_t written=0;while(written<size) {ssize_t w=write(fd,buffer+written,size-written);if(w<0 && errno==EINTR) continue;if(w<=0) break;written+=(size_t)w;}
 if(written!=size || fsync(fd)) goto done;
 if(close(fd)) {fd=-1;goto done;}fd=-1;
 if(unlinkat(dir,pending,0)) goto done;
 status=nx_bad?74:nx_readable?0:69;
 close(pfd);free(buffer);close(lfd);close(dir);return status;
 done:
 if(nx_out) fclose(nx_out);
 if(fd>=0) close(fd);
 if(pfd>=0) close(pfd);
 free(buffer);close(lfd);close(dir);return 74;
}
#endif
