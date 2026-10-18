/* Read-only Linux DCB configuration. Contract: docs/network-dcb-resources.md. */
#ifndef REMORA_NETDCB_H
#define REMORA_NETDCB_H
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
#include <sys/utsname.h>
#include <fcntl.h>
#include <unistd.h>
#include <errno.h>
#include <ctype.h>
#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <dirent.h>
#include <stddef.h>
#if defined(__linux__) && !defined(REMORA_DCB_TEST)
#include <sys/socket.h>
#include <net/if.h>
#include <linux/dcbnl.h>
#include <linux/rtnetlink.h>
#include <poll.h>
#endif
#define NC_IFS 16
#define NC_STATS 16384
#define NC_NAME 32
static FILE *pn_out;
static unsigned pn_records,pn_readable;
static int pn_bad,pn_first=1;
static const char *pn_limit,*nc_reason;
static void pn_quote_n(const char *s,size_t n) {
 if(!s) {fputs("null",pn_out);return;}
 fputc('"',pn_out);
 for(size_t i=0;i<n;i++) {
  unsigned char c=(unsigned char)s[i];
  if(c=='"' || c=='\\') {fputc('\\',pn_out);fputc(c,pn_out);}
  else if(c<32 || c>=127) fprintf(pn_out,"\\u%04x",c);
  else fputc(c,pn_out);
 }
 fputc('"',pn_out);
}
static void pn_quote(const char *s) {pn_quote_n(s,s?strlen(s):0);}
static const char *pn_status(int e) {
 if(!e) return "OK";
 if(e==ENOENT || e==ENODEV) return "ABSENT";
 if(e==EACCES || e==EPERM) return "DENIED";
 if(e==EINVAL || e==ENOSYS || e==EOPNOTSUPP) return "UNSUPPORTED";
 if(e==EBADMSG || e==ERANGE) return "MALFORMED";
 return "ERROR";
}
static int pn_optional(int e) {return e==ENOENT || e==ENODEV || e==EINVAL || e==ENOSYS || e==EOPNOTSUPP;}
static int pn_name(const char *s) {
 if(!s || !*s || strlen(s)>120 || !strcmp(s,".") || !strcmp(s,"..")) return 0;
 for(;*s;s++) if(!isalnum((unsigned char)*s) && !strchr("_.:-",*s)) return 0;
 return 1;
}
static int pn_number(const char *s,unsigned base,uint64_t max,uint64_t *value) {
 if(!s || !*s) return -1;
 uint64_t v=0;
 for(;*s;s++) {
  unsigned d=isdigit((unsigned char)*s)?(unsigned)(*s-'0'):
   base==16 && *s>='a' && *s<='f'?(unsigned)(*s-'a'+10):
   base==16 && *s>='A' && *s<='F'?(unsigned)(*s-'A'+10):99;
  if(d>=base || d>max || v>(max-d)/base) return -1;
  v=v*base+d;
 }
 *value=v;return 0;
}
static void pn_emit(const char *entity,const char *scope,const char *metric,const char *source,
                    const char *kind,const char *unit,const char *raw,const char *value,int error) {
 if(pn_records>=32768 || ftell(pn_out)>64*1024*1024-65536) {pn_bad=1;pn_limit="sample_record_or_byte_limit";return;}
 if(!pn_first) fputc(',',pn_out);
 pn_first=0;pn_records++;
 fputs("{\"entity\":",pn_out);pn_quote(entity);fputs(",\"scope\":",pn_out);pn_quote(scope);
 fputs(",\"metric\":",pn_out);pn_quote(metric);fputs(",\"source\":",pn_out);pn_quote(source);
 fputs(",\"kind\":",pn_out);pn_quote(kind);fputs(",\"unit\":",pn_out);pn_quote(unit);
 fprintf(pn_out,",\"byte_scale\":0,\"status\":");pn_quote(pn_status(error));
 fprintf(pn_out,",\"errno\":%d,\"raw\":",error);pn_quote(raw);
 fputs(",\"value\":",pn_out);pn_quote(error?NULL:value);fputs(",\"availability_reason\":",pn_out);pn_quote(nc_reason);fputc('}',pn_out);
 if(!error && (!strcmp(kind,"gauge") || !strcmp(kind,"metadata"))) pn_readable++;
 if(error && !pn_optional(error)) pn_bad=1;
}
static int pn_open(int dir,const char *name,int flags) {
 int fd=openat(dir,name,flags|O_NOFOLLOW|O_NONBLOCK,0600);if(fd<0) return -1;
 struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || st.st_nlink!=1) {close(fd);errno=EPERM;return -1;}return fd;
}
static void pn_time(const char *key,clockid_t clock) {
 struct timespec t;if(clock_gettime(clock,&t)) {pn_bad=1;t.tv_sec=0;t.tv_nsec=0;}
 fprintf(pn_out,"\"%s\":\"%" PRId64 ".%09ld\"",key,(int64_t)t.tv_sec,t.tv_nsec);
}

#define DC_BYTES 65536
struct nc_device {
 char name[32],driver[256],driver_after[256],type[32],state[32];
 unsigned index_before,index_after;int error,pfc_error,ets_error;
 const char *stage;unsigned char request[256],response[DC_BYTES];
 size_t request_len,response_len,received_len;uint32_t sequence,peer_pid;
 unsigned pfc_cap,pfc_mask,ets_cap;unsigned char mapping[8];
};
static const char *nc_sys="/sys";
static int nc_text(const char *iface,const char *field,char *out,size_t cap) {
 char path[1024];int n=snprintf(path,sizeof path,"%s/class/net/%s/%s",nc_sys,iface,field);
 if(n<0 || n>=(int)sizeof path) return ENAMETOOLONG;
 int fd=open(path,O_RDONLY|O_NONBLOCK);if(fd<0) return errno;
 ssize_t r=read(fd,out,cap-1);int e=r<0?errno:0;close(fd);
 if(r<=0) return e?e:EBADMSG;
 out[r]=0;while(r>0 && isspace((unsigned char)out[r-1])) out[--r]=0;
 return 0;
}
static int nc_compare(const void *a,const void *b) {return strcmp(*(char *const*)a,*(char *const*)b);}
static int nc_interface(const char *s) {return pn_name(s) && strlen(s)<16 && !strchr(s,':');}
static int nc_discover(struct nc_device *devices,unsigned *count) {
 const char *selection=getenv("REMORA_ETH_INTERFACES");char *names[NC_IFS];unsigned n=0;int error=0;
 if(selection) {
  size_t len=strlen(selection);if(!len || len>8192 || selection[0]==',' || selection[len-1]==',' || strstr(selection,",,")) return 65;
  char *copy=strdup(selection),*save=NULL;if(!copy) return 74;
  for(char *s=strtok_r(copy,",",&save);s;s=strtok_r(NULL,",",&save)) {
   if(!nc_interface(s)) {error=65;break;}
   if(n==NC_IFS) {error=74;pn_limit="interface_limit";break;}
   names[n]=strdup(s);if(!names[n]) {error=74;break;}n++;
  }
  free(copy);if(!n && !error) error=65;
 } else {
  char path[1024];snprintf(path,sizeof path,"%s/class/net",nc_sys);DIR *dir=opendir(path);
  if(!dir) return pn_optional(errno)?69:74;
  struct dirent *entry;
  while((entry=readdir(dir))) {
   if(!nc_interface(entry->d_name) || !strcmp(entry->d_name,"lo")) continue;
   char type[32]={0};if(nc_text(entry->d_name,"type",type,sizeof type)) {error=74;break;}
   if(strcmp(type,"1") && strcmp(type,"32")) continue;
   if(n==NC_IFS) {error=74;pn_limit="interface_limit";break;}
   names[n]=strdup(entry->d_name);if(!names[n]) {error=74;break;}n++;
  }
  closedir(dir);
 }
 qsort(names,n,sizeof *names,nc_compare);
 for(unsigned i=0;i<n;i++) {
  if(i && !strcmp(names[i-1],names[i])) error=65;
  snprintf(devices[i].name,sizeof devices[i].name,"%s",names[i]);
 }
 for(unsigned i=0;i<n;i++) free(names[i]);
 *count=n;return error?error:n?0:69;
}
static void nc_hex(const unsigned char *data,size_t size,int ok) {
 if(!ok) {fputs("null",pn_out);return;}
 fputc('"',pn_out);for(size_t i=0;i<size;i++) fprintf(pn_out,"%02x",data[i]);fputc('"',pn_out);
}

/* Native-endian Linux UAPI. memcpy avoids unaligned access on Arm. */
static unsigned dc_u16(const unsigned char *p) {uint16_t v;memcpy(&v,p,2);return v;}
static uint32_t dc_u32(const unsigned char *p) {uint32_t v;memcpy(&v,p,4);return v;}
static size_t dc_align(size_t n) {return (n+3)&~(size_t)3;}
static int dc_nested(struct nc_device *d,const unsigned char *p,size_t n) {
 unsigned seen=0;
 while(n) {
  if(n<4) return EBADMSG;
  unsigned len=dc_u16(p),type=dc_u16(p+2)&0x3fffU;
  if(len<4 || len>n || dc_align(len)>n) return EBADMSG;
  if(type==1 || type==2) {
   unsigned bit=1U<<type;if(seen&bit) return EBADMSG;seen|=bit;
   const unsigned char *v=p+4;size_t bytes=len-4;
   if(type==2) {
    if(bytes<136 || v[0]>8) return EBADMSG;
    d->pfc_cap=v[0];d->pfc_mask=v[1];d->pfc_error=0;
   } else {
    if(bytes<59 || v[1]>8) return EBADMSG;
    d->ets_cap=v[1];memcpy(d->mapping,v+27,8);
    for(unsigned i=0;i<8;i++) if(d->mapping[i]>7) return EBADMSG;
    d->ets_error=0;
   }
  }
  size_t step=dc_align(len);p+=step;n-=step;
 }
 return 0;
}
static int dc_parse(struct nc_device *d) {
 const unsigned char *p=d->response;size_t n=d->response_len;
 d->pfc_error=d->ets_error=ENOENT;
 if(d->received_len>DC_BYTES) return EOVERFLOW;
 if(d->peer_pid || n<16 || dc_u32(p)!=n || dc_u32(p+8)!=d->sequence) return EBADMSG;
 unsigned type=dc_u16(p+4),flags=dc_u16(p+6);
 if(flags&2U) return EBADMSG; /* multipart replies require a different protocol */
 if(type==2) { /* NLMSG_ERROR: full nlmsgerr, never treat ACK as data */
  if(n<36) return EBADMSG;
  int32_t error;memcpy(&error,p+16,4);
  return error<0 && error>=-4095 ? -error:EBADMSG;
 }
 if(type!=78 || n<20 || p[16]!=0 || p[17]!=21) return EBADMSG;
 p+=20;n-=20;unsigned seen=0;
 while(n) {
  if(n<4) return EBADMSG;
  unsigned len=dc_u16(p),a=dc_u16(p+2)&0x3fffU;
  if(len<4 || len>n || dc_align(len)>n) return EBADMSG;
  if(a==1 || a==13) {
   unsigned bit=a==1?1:2;if(seen&bit) return EBADMSG;seen|=bit;
   if(a==1) {
    size_t want=strlen(d->name)+1;
    if(len-4!=want || memcmp(p+4,d->name,want)) return EBADMSG;
   } else {int e=dc_nested(d,p+4,len-4);if(e) return e;}
  }
  size_t step=dc_align(len);p+=step;n-=step;
 }
 if(!(seen&1)) return EBADMSG;
 return 0;
}
static int dc_identity(struct nc_device *d,int after) {
 char text[32],path[1024];uint64_t index;
 int e=nc_text(d->name,"ifindex",text,sizeof text);if(e) return e;
 if(pn_number(text,10,UINT32_MAX,&index) || !index) return EBADMSG;
 if(after) d->index_after=(unsigned)index;else d->index_before=(unsigned)index;
 snprintf(path,sizeof path,"%s/class/net/%s/device/driver",nc_sys,d->name);
 char *dest=after?d->driver_after:d->driver;
 ssize_t n=readlink(path,dest,255);
 if(n<0) {if(errno!=ENOENT) return errno;dest[0]=0;}
 else {if(n==255) return EOVERFLOW;dest[n]=0;}
 if(after && (d->index_before!=d->index_after || strcmp(d->driver,d->driver_after))) return ESTALE;
 return 0;
}
#if defined(__linux__) && !defined(REMORA_DCB_TEST)
static int dc_query(struct nc_device *d) {
 /* Fail closed if this platform's UAPI is incompatible with the wire parser. */
 if(sizeof(struct ieee_pfc)!=136 || sizeof(struct ieee_ets)!=59 ||
    offsetof(struct ieee_ets,prio_tc)!=27 || DCB_ATTR_IEEE!=13 ||
    DCB_ATTR_IEEE_PFC!=2 || DCB_ATTR_IEEE_ETS!=1 || DCB_CMD_IEEE_GET!=21 || RTM_GETDCB!=78) return EOPNOTSUPP;
 struct nlmsghdr h={0};struct dcbmsg m={0};struct nlattr a={0};
 struct sockaddr_nl peer={0};socklen_t peer_len=sizeof peer;
 h.nlmsg_type=RTM_GETDCB;h.nlmsg_flags=NLM_F_REQUEST;h.nlmsg_seq=d->sequence=(uint32_t)getpid();
 m.dcb_family=AF_UNSPEC;m.cmd=DCB_CMD_IEEE_GET;
 a.nla_type=DCB_ATTR_IFNAME;a.nla_len=(uint16_t)(4+strlen(d->name)+1);
 h.nlmsg_len=(uint32_t)(20+dc_align(a.nla_len));d->request_len=h.nlmsg_len;
 memcpy(d->request,&h,16);memcpy(d->request+16,&m,4);memcpy(d->request+20,&a,4);memcpy(d->request+24,d->name,strlen(d->name)+1);
 int sock=socket(AF_NETLINK,SOCK_RAW,NETLINK_ROUTE);if(sock<0) return errno;
 peer.nl_family=AF_NETLINK;int error=0;
 if(sendto(sock,d->request,d->request_len,0,(struct sockaddr*)&peer,sizeof peer)!=(ssize_t)d->request_len) {error=errno?errno:EIO;goto done;}
 struct pollfd wait={sock,POLLIN,0};int ready=poll(&wait,1,1000);
 if(ready!=1) {error=ready==0?ETIMEDOUT:errno;goto done;}
 ssize_t n=recvfrom(sock,d->response,DC_BYTES,MSG_TRUNC,(struct sockaddr*)&peer,&peer_len);
 if(n<0) {error=errno;goto done;}
 d->received_len=(size_t)n;d->response_len=(size_t)n>DC_BYTES?DC_BYTES:(size_t)n;d->peer_pid=peer.nl_pid;
 if(peer_len!=sizeof peer || peer.nl_family!=AF_NETLINK) {error=EBADMSG;goto done;}
 error=dc_parse(d);
 done:close(sock);return error;
}
#elif defined(REMORA_DCB_TEST)
static int dc_query(struct nc_device *d) {
 d->sequence=77;const char *wait=getenv("REMORA_DCB_TEST_WAIT");
 if(wait) {int fd=open(wait,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd>=0) close(fd);for(;;) pause();}
 char path[1024];snprintf(path,sizeof path,"%.700s/class/net/%.15s/reply",nc_sys,d->name);
 int fd=open(path,O_RDONLY);if(fd<0) return errno;
 ssize_t n=read(fd,d->response,DC_BYTES);int e=n<0?errno:0;close(fd);if(e) return e;
 d->received_len=d->response_len=(size_t)n;
 if(getenv("REMORA_DCB_TEST_PEER")) d->peer_pid=1;
 if(getenv("REMORA_DCB_TEST_TRUNC")) d->received_len=DC_BYTES+1;
 return dc_parse(d);
}
#else
static int dc_query(struct nc_device *d) {(void)d;return EOPNOTSUPP;}
#endif
static void nc_fetch(struct nc_device *d) {
 d->pfc_error=d->ets_error=ENOENT;
 d->stage="identity_before";d->error=dc_identity(d,0);if(d->error) return;
 if(strcmp(d->type,"1")) {d->stage="not_applicable_to_transport";d->error=EOPNOTSUPP;return;}
 d->stage="DCB_CMD_IEEE_GET";d->error=dc_query(d);
 int query_error=d->error;d->stage="identity_after";int e=dc_identity(d,1);
 char type_after[32];int te=nc_text(d->name,"type",type_after,sizeof type_after);
 if(!e && (te || strcmp(type_after,d->type))) e=te?te:ESTALE;
#ifdef REMORA_DCB_TEST
 if(getenv("REMORA_DCB_TEST_RACE")) e=ESTALE;
#endif
 if(e) {d->error=e;return;}
 d->error=query_error;
 d->stage=d->error==EOPNOTSUPP?"kernel_get_not_supported":d->error==EACCES || d->error==EPERM?"kernel_get_denied":d->error?"get_or_response_validation":"complete";
}
static void dc_field(struct nc_device *d,const char *metric,const char *scope,const char *kind,const char *unit,unsigned value,int group_error) {
 int e=d->error?d->error:group_error;char v[32];snprintf(v,sizeof v,"%u",value);
 nc_reason=d->error?d->stage:group_error?"response_attribute_not_exposed":NULL;
 pn_emit(d->name,scope,metric,"RTM_GETDCB/DCB_CMD_IEEE_GET",kind,unit,e?NULL:v,v,e);nc_reason=NULL;
}
static void nc_records(struct nc_device *d,const char *module) {
 (void)module;
 dc_field(d,"pfc_cap","netdev_dcb_configuration","metadata","traffic_classes",d->pfc_cap,d->pfc_error);
 dc_field(d,"pfc_enable_mask","netdev_dcb_configuration","gauge","bitmask",d->pfc_mask,d->pfc_error);
 dc_field(d,"ets_cap","netdev_dcb_configuration","metadata","traffic_classes",d->ets_cap,d->ets_error);
 for(unsigned i=0;i<8;i++) {
  char key[64];snprintf(key,sizeof key,"priority_%u_pfc_enabled",i);
  dc_field(d,key,"netdev_dcb_priority_configuration","gauge","boolean",(d->pfc_mask>>i)&1,d->pfc_error);
  snprintf(key,sizeof key,"priority_%u_to_tc",i);
  dc_field(d,key,"netdev_dcb_priority_configuration","gauge","index",d->mapping[i],d->ets_error);
 }
}
static void nc_snapshot(struct nc_device *d) {
 fputs("{\"entity\":",pn_out);pn_quote(d->name);fputs(",\"source\":\"RTM_GETDCB/DCB_CMD_IEEE_GET\",\"stage\":",pn_out);pn_quote(d->stage);
 fprintf(pn_out,",\"errno\":%d,\"pfc_errno\":%d,\"ets_errno\":%d,\"ifindex_before\":%u,\"ifindex_after\":%u,\"sequence\":%u,\"peer_pid\":%u,\"received_bytes\":%zu,\"request_hex\":",d->error,d->pfc_error,d->ets_error,d->index_before,d->index_after,d->sequence,d->peer_pid,d->received_len);
 nc_hex(d->request,d->request_len,1);fputs(",\"response_hex\":",pn_out);nc_hex(d->response,d->response_len,1);
 fputs(",\"driver_before\":",pn_out);pn_quote(d->driver);fputs(",\"driver_after\":",pn_out);pn_quote(d->driver_after);fputc('}',pn_out);
}
static int remora_netdcb_main(const char *module,int argc,char **argv) {
 if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--check")) || !pn_name(argv[1])) return 65;
#ifdef REMORA_DCB_TEST
 const char *root=getenv("REMORA_NETWORK_SYSFS_ROOT");if(root) nc_sys=root;
#endif
 static struct nc_device devices[NC_IFS];memset(devices,0,sizeof devices);unsigned count=0;
 int discovery=nc_discover(devices,&count);if(discovery==65) return 65;
 if(argc==5) {
  if(discovery) return discovery;
  unsigned present=0;int bad=0;
  for(unsigned i=0;i<count;i++) {char type[32];int e=nc_text(devices[i].name,"type",type,sizeof type);if(!e) present++;else if(!pn_optional(e)) bad=1;}
  return bad?74:present?0:69;
 }
 if(discovery==74) pn_bad=1;
 int status=74,dir=open(argv[3],O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0) return 74;
 char filename[256],lock[280],pending[280];snprintf(filename,sizeof filename,"%s_%s.jsonl",module,argv[1]);snprintf(lock,sizeof lock,"%s.lock",filename);snprintf(pending,sizeof pending,"%s.pending",filename);
 int lfd=pn_open(dir,lock,O_RDWR|O_CREAT);if(lfd<0) {close(dir);return 74;}
 if(flock(lfd,LOCK_EX|LOCK_NB)) {close(lfd);close(dir);return 75;}
 int pfd=pn_open(dir,pending,O_WRONLY|O_CREAT|O_EXCL),fd=-1;char *buffer=NULL;size_t size=0;
 if(pfd<0) goto done;
 fd=pn_open(dir,filename,O_RDWR|O_APPEND|O_CREAT);if(fd<0) goto done;
 struct stat st;if(fstat(fd,&st)) goto done;
 if(st.st_size) {char last;if(pread(fd,&last,1,st.st_size-1)!=1 || last!='\n') goto done;}
 pn_out=open_memstream(&buffer,&size);if(!pn_out) goto done;
 fputs("{\"schema\":\"remora-network-resources/v1\",\"module\":",pn_out);pn_quote(module);fputs(",\"node\":",pn_out);pn_quote(argv[1]);
 struct utsname kernel;fputs(",\"kernel\":",pn_out);pn_quote(uname(&kernel)?"UNKNOWN":kernel.release);
 char ns[256],boot[128]={0};ssize_t n=readlink("/proc/self/ns/net",ns,sizeof ns-1);if(n>=0) ns[n]=0;
 fputs(",\"network_namespace\":",pn_out);pn_quote(n<0?"UNKNOWN":ns);
 int bfd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_NONBLOCK);if(bfd>=0) {n=read(bfd,boot,sizeof boot-1);if(n>0) {boot[n]=0;while(n>0 && isspace((unsigned char)boot[n-1])) boot[--n]=0;}close(bfd);}
 fputs(",\"boot_id\":",pn_out);pn_quote(*boot?boot:"UNKNOWN");fputc(',',pn_out);pn_time("wall_start",CLOCK_REALTIME);fputc(',',pn_out);pn_time("mono_start",CLOCK_MONOTONIC);
 fputs(",\"entities\":[",pn_out);
 for(unsigned i=0;i<count;i++) {
  struct nc_device *d=&devices[i];int e=nc_text(d->name,"type",d->type,sizeof d->type);nc_text(d->name,"operstate",d->state,sizeof d->state);
  if(e) {d->error=e;d->stage="interface_metadata";}else nc_fetch(d);
  if(d->error && !pn_optional(d->error)) pn_bad=1;
  if(i) fputc(',',pn_out);
  fputs("{\"name\":",pn_out);pn_quote(d->name);fputs(",\"family_evidence\":\"sysfs_transport_and_driver\",\"driver\":",pn_out);pn_quote(d->driver);
  fputs(",\"type\":",pn_out);pn_quote(d->type);fputs(",\"operstate\":",pn_out);pn_quote(d->state);fputs(",\"api_status\":",pn_out);pn_quote(pn_status(d->error));fputc('}',pn_out);
 }
 fputs("],\"records\":[",pn_out);for(unsigned i=0;i<count;i++) nc_records(&devices[i],module);
 if(!count) pn_emit("interfaces","netdev","discovery","/sys/class/net","availability","none",NULL,NULL,discovery==74?EIO:ENOENT);
 fputs("],\"source_snapshots\":[",pn_out);for(unsigned i=0;i<count;i++) {if(i) fputc(',',pn_out);nc_snapshot(&devices[i]);}
 fputs("],",pn_out);pn_time("wall_end",CLOCK_REALTIME);fputc(',',pn_out);pn_time("mono_end",CLOCK_MONOTONIC);fputs(",\"limitation\":",pn_out);pn_quote(pn_limit);
 fprintf(pn_out,",\"status\":\"%s\",\"readable_primary\":%u,\"record_count\":%u}\n",pn_bad?"PARTIAL":pn_readable?"OK":"UNSUPPORTED",pn_readable,pn_records);
 int bad=ferror(pn_out);if(fclose(pn_out)) bad=1;pn_out=NULL;
 if(bad || size>64*1024*1024) goto done;
 size_t written=0;while(written<size) {ssize_t w=write(fd,buffer+written,size-written);if(w<0 && errno==EINTR) continue;if(w<=0) break;written+=(size_t)w;}
 if(written!=size || fsync(fd)) goto done;
 if(close(fd)) {fd=-1;goto done;}fd=-1;
 if(unlinkat(dir,pending,0)) goto done;
 status=pn_bad?74:pn_readable?0:69;
 done:
 if(pn_out) fclose(pn_out);
 if(fd>=0) close(fd);
 if(pfd>=0) close(pfd);
 free(buffer);close(lfd);close(dir);return status;
}
#endif
