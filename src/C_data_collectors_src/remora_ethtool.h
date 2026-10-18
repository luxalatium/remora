/* Read-only standard Ethernet pause/FEC. Contract: docs/network-ethtool-resources.md. */
#ifndef REMORA_ETHTOOL_H
#define REMORA_ETHTOOL_H
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
#if defined(__linux__) && !defined(REMORA_ETHTOOL_TEST)
#include <sys/socket.h>
#include <net/if.h>
#include <linux/genetlink.h>
#include <linux/sockios.h>
#include <sys/ioctl.h>
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
 if(!error && (!strcmp(kind,"counter") || !strcmp(kind,"gauge") || !strcmp(kind,"metadata")) && strcmp(metric,"stats_source") && strncmp(metric,"ioctl_",6)) pn_readable++;
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


#define EC_BYTES 65536
#define EC_FIELDS 160
struct ec_wire {unsigned char request[256],response[EC_BYTES];size_t request_len,response_len,received_len;uint32_t sequence,peer_pid;int error,attempted;};
struct ec_field {char name[80],value[64];const char *unit,*kind,*scope,*source,*reason;int error;};
struct nc_device {char name[32],driver[256],driver_after[256],type[32],state[32];unsigned index_before,index_after;int error;const char *stage;struct ec_wire wires[3];unsigned fields;struct ec_field field[EC_FIELDS];};
static const char *ec_module;
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

static int ec_identity(struct nc_device *d,int after) {
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
struct ec_attr {const unsigned char *p;size_t n;int seen;};
static uint32_t ec_sequence;
static unsigned ec_u16(const unsigned char *p) {uint16_t v;memcpy(&v,p,2);return v;}
static uint32_t ec_u32(const unsigned char *p) {uint32_t v;memcpy(&v,p,4);return v;}
static uint64_t ec_u64(const unsigned char *p) {uint64_t v;memcpy(&v,p,8);return v;}
static size_t ec_align(size_t n) {return (n+3)&~(size_t)3;}
static int ec_attrs(const unsigned char *p,size_t n,struct ec_attr *a,unsigned max,int pad) {
 while(n) {
  if(n<4) return EBADMSG;
  unsigned len=ec_u16(p),flags=ec_u16(p+2),t=flags&0x3fffU;
  if(len<4 || len>n || ec_align(len)>n) return EBADMSG;
  if(t && t<=max && !(pad && t==1)) {
   if(a[t].seen || (flags&0x4000)) return EBADMSG;
   a[t].seen=1;a[t].p=p+4;a[t].n=len-4;
  }
  size_t step=ec_align(len);p+=step;n-=step;
 }
 return 0;
}
static int ec_header(struct ec_wire *w,unsigned family,unsigned command) {
 const unsigned char *p=w->response;size_t n=w->response_len;
 if(w->received_len>EC_BYTES) return EOVERFLOW;
 if(w->peer_pid || n<16 || ec_u32(p)!=n || ec_u32(p+8)!=w->sequence || (ec_u16(p+6)&2)) return EBADMSG;
 unsigned type=ec_u16(p+4);
 if(type==2) {
  if(n<36) return EBADMSG;
  int32_t error;memcpy(&error,p+16,4);return error<0 && error>=-4095?-error:EBADMSG;
 }
 if(type!=family || n<20 || p[16]!=command) return EBADMSG;
 return 0;
}
static struct ec_field *ec_add(struct nc_device *d,const char *name,const char *kind,const char *unit,const char *scope) {
 if(d->fields==EC_FIELDS) {d->error=EOVERFLOW;pn_limit="field_limit";return NULL;}
 struct ec_field *f=&d->field[d->fields++];snprintf(f->name,sizeof f->name,"%s",name);f->kind=kind;f->unit=unit;f->scope=scope;f->source="generic_netlink/ethtool";f->error=ENOENT;f->reason="response_attribute_not_exposed";return f;
}
static struct ec_field *ec_find(struct nc_device *d,const char *name) {
 for(unsigned i=0;i<d->fields;i++) if(!strcmp(d->field[i].name,name)) return &d->field[i];
 return NULL;
}
static void ec_value(struct ec_field *f,uint64_t v) {if(f) {snprintf(f->value,sizeof f->value,"%" PRIu64,v);f->error=0;f->reason=NULL;}}
static void ec_defaults(struct nc_device *d) {
 if(!strcmp(ec_module,"netpause")) {
  const char *names[]={"autoneg","rx_pause","tx_pause"};
  for(unsigned i=0;i<3;i++) ec_add(d,names[i],"gauge","boolean","netdev_ethtool_configuration");
  ec_add(d,"stats_source","metadata","enum_index","physical_port_mac");
  ec_add(d,"tx_pause_frames","counter","frames","physical_port_mac_aggregate");ec_add(d,"rx_pause_frames","counter","frames","physical_port_mac_aggregate");
 } else {
  const char *names[]={"auto_selection","configured_off","configured_rs","configured_baser","configured_llrs"};
  for(unsigned i=0;i<5;i++) ec_add(d,names[i],"gauge","boolean","netdev_ethtool_configuration");
  ec_add(d,"active_mode_index","gauge","link_mode_index","netdev_ethtool_active_mode");ec_add(d,"active_mode","metadata","enum","netdev_ethtool_active_mode");
  const char *stats[]={"corrected_blocks","uncorrectable_blocks","corrected_bits"};
  for(unsigned i=0;i<3;i++) {
   ec_add(d,stats[i],"counter",i==2?"bits":"blocks","physical_port_fec");char name[80];
   snprintf(name,sizeof name,"%s_lane_count",stats[i]);ec_add(d,name,"metadata","entries","source_lane_or_PCS_entries");
   snprintf(name,sizeof name,"%s_lane_breakdown",stats[i]);ec_add(d,name,"availability","none","physical_port_lane_or_PCS_instance");
  }
  struct ec_field *f=ec_add(d,"per_lane_link_state","availability","none","physical_port_lane_or_PCS_instance");
  f->error=EOPNOTSUPP;f->reason="not_defined_by_standard_fec_get";
 }
}
static int ec_scalar(struct nc_device *d,const char *name,struct ec_attr *a,size_t size,int boolean) {
 if(!a->seen) return 0;
 if(a->n!=size) return EBADMSG;
 uint64_t v=size==1?a->p[0]:size==4?ec_u32(a->p):ec_u64(a->p);
 ec_value(ec_find(d,name),boolean?!!v:v);return 0;
}
static void ec_active(struct nc_device *d,unsigned index) {
 ec_value(ec_find(d,"active_mode_index"),index);struct ec_field *f=ec_find(d,"active_mode");
 snprintf(f->value,sizeof f->value,"%s",index==49?"Off":index==50?"RS":index==51?"BASER":index==74?"LLRS":"UNKNOWN");f->error=0;f->reason=NULL;
}
static int ec_bitset(struct nc_device *d,struct ec_attr *input) {
 if(!input->seen) return 0;
 struct ec_attr a[6]={{0}};int e=ec_attrs(input->p,input->n,a,5,0);if(e) return e;
 if(!a[2].seen || a[2].n!=4 || !a[4].seen || (a[1].seen && (a[1].n || a[5].seen)) || (!a[1].seen && !a[5].seen)) return EBADMSG;
 unsigned bits=ec_u32(a[2].p);if(bits>4096) return EOVERFLOW;
 size_t bytes=((bits+31)/32)*4;if(a[4].n!=bytes || (!a[1].seen && a[5].n!=bytes)) return EBADMSG;
 const unsigned indexes[]={49,50,51,74};const char *names[]={"configured_off","configured_rs","configured_baser","configured_llrs"};
 for(unsigned i=0;i<4;i++) {
  unsigned bit=indexes[i];if(bit>=bits) continue;
  uint32_t mask=1U<<(bit%32);if(a[1].seen || (ec_u32(a[5].p+(bit/32)*4)&mask)) ec_value(ec_find(d,names[i]),!!(ec_u32(a[4].p+(bit/32)*4)&mask));
 }
 return 0;
}
static int ec_decode(struct nc_device *d,struct ec_wire *w,unsigned family) {
 int pause=!strcmp(ec_module,"netpause"),e=ec_header(w,family,pause?22:30);if(e) return e;
 if(w->response[17]!=1) return EBADMSG;
 struct ec_attr a[7]={{0}},h[5]={{0}};e=ec_attrs(w->response+20,w->response_len-20,a,pause?6:5,0);if(e) return e;
 if(!a[1].seen) return EBADMSG;
 e=ec_attrs(a[1].p,a[1].n,h,4,0);if(e) return e;
 if(!h[1].seen && !h[2].seen) return EBADMSG;
 if(h[1].seen && (h[1].n!=4 || ec_u32(h[1].p)!=d->index_before)) return EBADMSG;
 if(h[2].seen && (h[2].n!=strlen(d->name)+1 || memcmp(h[2].p,d->name,h[2].n))) return EBADMSG;
 if(pause) {
  if((e=ec_scalar(d,"autoneg",&a[2],1,1)) || (e=ec_scalar(d,"rx_pause",&a[3],1,1)) || (e=ec_scalar(d,"tx_pause",&a[4],1,1)) || (e=ec_scalar(d,"stats_source",&a[6],4,0))) return e;
  struct ec_attr stats[4]={{0}};if(a[5].seen && (e=ec_attrs(a[5].p,a[5].n,stats,3,1))) return e;
  if((e=ec_scalar(d,"tx_pause_frames",&stats[2],8,0)) || (e=ec_scalar(d,"rx_pause_frames",&stats[3],8,0))) return e;
  unsigned source=a[6].seen?ec_u32(a[6].p):0;
  const char *scope=source==0?"physical_port_mac_aggregate":source==1?"physical_port_mac_emac":source==2?"physical_port_mac_pmac":"physical_port_mac_unknown_source";
  ec_find(d,"rx_pause_frames")->scope=scope;ec_find(d,"tx_pause_frames")->scope=scope;
 } else {
  if((e=ec_bitset(d,&a[2])) || (e=ec_scalar(d,"auto_selection",&a[3],1,1))) return e;
  if(a[4].seen) {if(a[4].n!=4) return EBADMSG;unsigned index=ec_u32(a[4].p);if(index>4095) return ERANGE;ec_active(d,index);}
  struct ec_attr stats[5]={{0}};if(a[5].seen && (e=ec_attrs(a[5].p,a[5].n,stats,4,1))) return e;
  const char *names[]={"corrected_blocks","uncorrectable_blocks","corrected_bits"};
  for(unsigned j=0;j<3;j++) {
   struct ec_attr *v=&stats[j+2];struct ec_field *f=ec_find(d,names[j]);char key[80];
   snprintf(key,sizeof key,"%s_lane_count",names[j]);struct ec_field *count=ec_find(d,key);
   snprintf(key,sizeof key,"%s_lane_breakdown",names[j]);struct ec_field *availability=ec_find(d,key);
   if(!v->seen) continue;
   if(v->n%8) return EBADMSG;
   size_t n=v->n/8;if(n>33) return EOVERFLOW;
   if(!n) {f->error=count->error=availability->error=EOPNOTSUPP;f->reason=count->reason=availability->reason="statistics_not_exposed";continue;}
   ec_value(f,ec_u64(v->p));
   if(n==1) {count->error=availability->error=EOPNOTSUPP;count->reason=availability->reason="aggregate_only_no_lane_breakdown";continue;}
   ec_value(count,n-1);availability->error=0;availability->reason=NULL;snprintf(availability->value,sizeof availability->value,"available");
   for(unsigned i=1;i<n;i++) {snprintf(key,sizeof key,"lane_%u_%s",i-1,names[j]);ec_value(ec_add(d,key,"counter",j==2?"bits":"blocks","physical_port_lane_or_PCS_instance"),ec_u64(v->p+i*8));}
  }
 }
 return d->error;
}
#if defined(__linux__) && !defined(REMORA_ETHTOOL_TEST)
static size_t ec_put(unsigned char *p,unsigned type,const void *v,size_t n) {
 uint16_t len=(uint16_t)(4+n),t=(uint16_t)type;memcpy(p,&len,2);memcpy(p+2,&t,2);memcpy(p+4,v,n);return ec_align(len);
}
static int ec_exchange(struct ec_wire *w,unsigned family,unsigned command,const unsigned char *attrs,size_t bytes) {
 w->attempted=1;w->sequence=++ec_sequence;w->request_len=20+bytes;
 uint32_t len=(uint32_t)w->request_len;uint16_t type=(uint16_t)family,flags=1;
 memcpy(w->request,&len,4);memcpy(w->request+4,&type,2);memcpy(w->request+6,&flags,2);memcpy(w->request+8,&w->sequence,4);
 w->request[16]=(unsigned char)command;w->request[17]=1;memcpy(w->request+20,attrs,bytes);
 int fd=socket(AF_NETLINK,SOCK_RAW,NETLINK_GENERIC);if(fd<0) return errno;
 struct sockaddr_nl peer={0};peer.nl_family=AF_NETLINK;int error=0;
 if(sendto(fd,w->request,w->request_len,0,(struct sockaddr*)&peer,sizeof peer)!=(ssize_t)w->request_len) {error=errno?errno:EIO;goto done;}
 struct pollfd wait={fd,POLLIN,0};int ready=poll(&wait,1,1000);
 if(ready!=1) {error=ready==0?ETIMEDOUT:errno;goto done;}
 socklen_t peer_len=sizeof peer;ssize_t n=recvfrom(fd,w->response,EC_BYTES,MSG_TRUNC,(struct sockaddr*)&peer,&peer_len);
 if(n<0) {error=errno;goto done;}
 w->received_len=(size_t)n;w->response_len=(size_t)n>EC_BYTES?EC_BYTES:(size_t)n;w->peer_pid=peer.nl_pid;
 if(peer_len!=sizeof peer || peer.nl_family!=AF_NETLINK || peer.nl_groups) error=EBADMSG;
 done:close(fd);return error;
}
static int ec_ioctl(struct nc_device *d) {
 struct ec_wire *w=&d->wires[2];uint32_t data[4]={!strcmp(ec_module,"netpause")?0x12U:0x50U,0,0,0};
 w->attempted=1;memcpy(w->request,data,sizeof data);w->request_len=sizeof data;
 int fd=socket(AF_INET,SOCK_DGRAM,0);if(fd<0) return errno;
 struct ifreq req;memset(&req,0,sizeof req);memcpy(req.ifr_name,d->name,strlen(d->name)+1);req.ifr_data=(void*)data;
 int e=ioctl(fd,SIOCETHTOOL,&req)<0?errno:0;close(fd);memcpy(w->response,data,sizeof data);w->response_len=w->received_len=sizeof data;return e;
}
static int ec_get(struct nc_device *d) {
 unsigned char attrs[128]={0};struct ec_wire *family=&d->wires[0],*w=&d->wires[1];
 size_t n=ec_put(attrs,2,"ethtool",8);int e=ec_exchange(family,16,3,attrs,n);
 if(!e) e=ec_header(family,16,1);
 struct ec_attr a[4]={{0}};unsigned id=0;
 if(!e) e=ec_attrs(family->response+20,family->response_len-20,a,3,0);
 if(!e && (!a[1].seen || a[1].n!=2 || !a[2].seen || a[2].n!=8 || memcmp(a[2].p,"ethtool",8))) e=EBADMSG;
 if(!e) {id=ec_u16(a[1].p);if(id<17) e=EBADMSG;}
 family->error=e;if(e) return e;
 memset(attrs,0,sizeof attrs);unsigned char header[80]={0};uint32_t flags=5,index=d->index_before;
 n=ec_put(header,1,&index,4);n+=ec_put(header+n,2,d->name,strlen(d->name)+1);n+=ec_put(header+n,3,&flags,4);
 n=ec_put(attrs,0x8001,header,n);e=ec_exchange(w,id,!strcmp(ec_module,"netpause")?21:29,attrs,n);
 if(!e) e=ec_decode(d,w,id);
 w->error=e;return e;
}
#elif defined(REMORA_ETHTOOL_TEST)
static int ec_get(struct nc_device *d) {
 struct ec_wire *w=&d->wires[1];w->attempted=1;w->sequence=77;char path[1024];
 const char *wait=getenv("REMORA_ETHTOOL_TEST_WAIT");if(wait) {int fd=open(wait,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd>=0) close(fd);for(;;) pause();}
 snprintf(path,sizeof path,"%.700s/class/net/%.15s/%.15s.reply",nc_sys,d->name,ec_module);
 int fd=open(path,O_RDONLY);if(fd<0) return w->error=errno;
 ssize_t n=read(fd,w->response,EC_BYTES);int e=n<0?errno:0;close(fd);if(e) return w->error=e;
 w->received_len=w->response_len=(size_t)n;if(getenv("REMORA_ETHTOOL_TEST_PEER")) w->peer_pid=1;
 if(getenv("REMORA_ETHTOOL_TEST_TRUNC")) w->received_len=EC_BYTES+1;
 return w->error=ec_decode(d,w,42);
}
static int ec_ioctl(struct nc_device *d) {
 char path[1024];struct ec_wire *w=&d->wires[2];w->attempted=1;
 snprintf(path,sizeof path,"%.700s/class/net/%.15s/%.15s.ioctl",nc_sys,d->name,ec_module);FILE *in=fopen(path,"rb");if(!in) return errno;
 size_t n=fread(w->response,1,17,in);fclose(in);w->response_len=w->received_len=n;return n==16?0:EBADMSG;
}
#else
static int ec_get(struct nc_device *d) {(void)d;return EOPNOTSUPP;}
static int ec_ioctl(struct nc_device *d) {(void)d;return EOPNOTSUPP;}
#endif
static int ec_fallback_decode(struct nc_device *d) {
 const unsigned char *p=d->wires[2].response;int pause=!strcmp(ec_module,"netpause");
 if(ec_u32(p)!=(pause?0x12U:0x50U)) return EBADMSG;
 if(pause) {
  const char *names[]={"autoneg","rx_pause","tx_pause"};for(unsigned i=0;i<3;i++) ec_value(ec_find(d,names[i]),!!ec_u32(p+4+i*4));
 } else {
  unsigned active=ec_u32(p+4),configured=ec_u32(p+8);
  ec_value(ec_add(d,"ioctl_active_fec_mask","metadata","legacy_fec_bitmask","netdev_ethtool_configuration"),active);
  ec_value(ec_add(d,"ioctl_configured_fec_mask","metadata","legacy_fec_bitmask","netdev_ethtool_configuration"),configured);
  if(configured&1U) {
   const char *names[]={"auto_selection","configured_off","configured_rs","configured_baser","configured_llrs"};
   for(unsigned i=0;i<5;i++) {struct ec_field *f=ec_find(d,names[i]);f->error=EOPNOTSUPP;f->reason="driver_reports_fec_not_supported";}
  } else {
   const char *names[]={"auto_selection","configured_off","configured_rs","configured_baser","configured_llrs"};
   for(unsigned i=0;i<5;i++) ec_value(ec_find(d,names[i]),!!(configured&(1U<<(i+1))));
  }
  if(active && !(active&(active-1)) && !(active&~60U)) ec_active(d,active==4?49:active==8?50:active==16?51:74);
  else {struct ec_field *f=ec_find(d,"active_mode_index"),*g=ec_find(d,"active_mode");f->error=g->error=EOPNOTSUPP;f->reason=g->reason=active&1U?"driver_reports_fec_not_supported":"active_mode_not_resolved";}
 }
 for(unsigned i=0;i<d->fields;i++) {
  struct ec_field *f=&d->field[i];if(!f->error) f->source="SIOCETHTOOL/GET_fallback";
  else if(f->reason && !strcmp(f->reason,"response_attribute_not_exposed")) f->reason="netlink_unavailable_ioctl_has_no_statistics";
 }
 return 0;
}
static void nc_fetch(struct nc_device *d) {
 ec_defaults(d);d->stage="identity_before";d->error=ec_identity(d,0);if(d->error) return;
 if(strcmp(d->type,"1")) {d->stage="not_applicable_to_transport";d->error=EOPNOTSUPP;return;}
 d->stage="ethtool_GET";int e=ec_get(d);
 if(e==EOPNOTSUPP || e==ENOSYS || e==ENOENT || e==EINVAL) {
  int ie=ec_ioctl(d);d->wires[2].error=ie;if(!ie) ie=ec_fallback_decode(d);
  d->wires[2].error=ie;e=ie;
 }
 d->error=e;int identity=ec_identity(d,1);char type[32];int te=nc_text(d->name,"type",type,sizeof type);
 if(!identity && (te || strcmp(type,d->type))) identity=te?te:ESTALE;
#ifdef REMORA_ETHTOOL_TEST
 if(getenv("REMORA_ETHTOOL_TEST_RACE")) identity=ESTALE;
#endif
 if(identity) {d->error=identity;d->stage="identity_after";return;}
 d->stage=e==EOPNOTSUPP?"kernel_get_not_supported":e==EACCES || e==EPERM?"kernel_get_denied":e?"get_or_response_validation":"complete";
}
static void nc_records(struct nc_device *d,const char *module) {
 (void)module;if(!d->fields) ec_defaults(d);
 for(unsigned i=0;i<d->fields;i++) {
  struct ec_field *f=&d->field[i];int e=d->error?d->error:f->error;
  nc_reason=d->error?d->stage:f->reason;pn_emit(d->name,f->scope,f->name,f->source,f->kind,f->unit,e?NULL:f->value,f->value,e);
 }
 nc_reason=NULL;
}
static void nc_snapshot(struct nc_device *d) {
 fputs("{\"entity\":",pn_out);pn_quote(d->name);fputs(",\"stage\":",pn_out);pn_quote(d->stage);
 fprintf(pn_out,",\"errno\":%d,\"ifindex_before\":%u,\"ifindex_after\":%u,\"driver_before\":",d->error,d->index_before,d->index_after);pn_quote(d->driver);fputs(",\"driver_after\":",pn_out);pn_quote(d->driver_after);
 fputs(",\"exchanges\":[",pn_out);
 for(unsigned i=0;i<3;i++) {
  struct ec_wire *w=&d->wires[i];if(i) fputc(',',pn_out);
  fprintf(pn_out,"{\"api\":\"%s\",\"attempted\":%s,\"errno\":%d,\"sequence\":%u,\"peer_pid\":%u,\"received_bytes\":%zu,\"request_hex\":",i==0?"GENL_CTRL_GETFAMILY":i==1?"ethtool_GET":"SIOCETHTOOL_GET",w->attempted?"true":"false",w->error,w->sequence,w->peer_pid,w->received_len);
  nc_hex(w->request,w->request_len,1);fputs(",\"response_hex\":",pn_out);nc_hex(w->response,w->response_len,1);fputc('}',pn_out);
 }
 fputs("]}",pn_out);
}
static int remora_ethtool_main(const char *module,int argc,char **argv) {
 if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--check")) || !pn_name(argv[1])) return 65;
#ifdef REMORA_ETHTOOL_TEST
 const char *root=getenv("REMORA_NETWORK_SYSFS_ROOT");if(root) nc_sys=root;
#endif
 ec_module=module;
 ec_sequence=(uint32_t)getpid();
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
