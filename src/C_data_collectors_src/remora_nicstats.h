/* Read-only Linux ethtool driver statistics. See docs/network-nic-resources.md. */
#ifndef REMORA_NICSTATS_H
#define REMORA_NICSTATS_H
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
#if defined(__linux__) && !defined(REMORA_NIC_TEST)
#include <sys/socket.h>
#include <sys/ioctl.h>
#include <net/if.h>
#include <linux/ethtool.h>
#include <linux/sockios.h>
#endif
#define NC_IFS 16
#define NC_STATS 16384
#define NC_NAME 32
static FILE *pn_out;
static unsigned pn_records,pn_readable;
static int pn_bad,pn_first=1;
static const char *pn_limit;
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
 fputs(",\"value\":",pn_out);pn_quote(error?NULL:value);fputc('}',pn_out);
 if(!error && !strcmp(kind,"counter")) pn_readable++;
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
#include "remora_nicfields.h"
struct nc_device {
 char name[32],driver[33],version[33],firmware[33],bus[33],type[32],state[32];
 unsigned index_before,index_after,count;int error,names_ok,values_ok,names_after_ok,info_ok,info_after_ok;
 const char *stage;
 unsigned char info[256],info_after[256];size_t info_len;
 unsigned char *names,*names_after;uint64_t *values;
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
#if defined(__linux__) || defined(REMORA_NIC_TEST)
static int nc_names_valid(struct nc_device *d) {
 char **sorted=calloc(d->count,sizeof *sorted);if(!sorted) return ENOMEM;
 int error=0;
 for(unsigned i=0;i<d->count;i++) {
  char *s=(char*)d->names+i*NC_NAME;sorted[i]=s;
  if(!memchr(s,0,NC_NAME) || !*s) {error=EBADMSG;break;}
  for(unsigned j=0;s[j];j++) if((unsigned char)s[j]<32 || (unsigned char)s[j]>=127) error=EBADMSG;
 }
 if(!error) {qsort(sorted,d->count,sizeof *sorted,nc_compare);for(unsigned i=1;i<d->count;i++) if(!strcmp(sorted[i-1],sorted[i])) error=EBADMSG;}
 free(sorted);return error;
}
#endif
#if defined(__linux__) && !defined(REMORA_NIC_TEST)
static int nc_get(int sock,const char *name,void *data) {
 struct ifreq request;memset(&request,0,sizeof request);
 memcpy(request.ifr_name,name,strlen(name)+1);request.ifr_data=data;
 return ioctl(sock,SIOCETHTOOL,&request)<0?errno:0;
}
static void nc_fetch(struct nc_device *d) {
 int sock=socket(AF_INET,SOCK_DGRAM,0);struct ethtool_drvinfo before,after;
 struct ethtool_gstrings *names=NULL,*names_after=NULL;struct ethtool_stats *stats=NULL;
 memset(&before,0,sizeof before);memset(&after,0,sizeof after);
 d->stage="socket";if(sock<0) {d->error=errno;return;}
 d->index_before=if_nametoindex(d->name);
 if(!d->index_before) {d->error=errno?errno:ENODEV;goto done;}
 d->stage="GDRVINFO_before";before.cmd=ETHTOOL_GDRVINFO;d->error=nc_get(sock,d->name,&before);if(d->error) goto done;
 d->info_ok=1;d->info_len=sizeof before;memcpy(d->info,&before,sizeof before);
 memcpy(d->driver,before.driver,32);memcpy(d->version,before.version,32);memcpy(d->firmware,before.fw_version,32);memcpy(d->bus,before.bus_info,32);
 d->count=before.n_stats;
 d->stage="stat_count";if(d->count>NC_STATS) {d->error=EOVERFLOW;pn_limit="driver_stat_limit";goto done;}if(!d->count) {d->error=EOPNOTSUPP;goto done;}
 names=calloc(1,sizeof *names+d->count*NC_NAME);names_after=calloc(1,sizeof *names_after+d->count*NC_NAME);stats=calloc(1,sizeof *stats+d->count*sizeof(uint64_t));
 if(!names || !names_after || !stats) {d->error=ENOMEM;goto done;}
 names->cmd=ETHTOOL_GSTRINGS;names->string_set=ETH_SS_STATS;names->len=d->count;
 d->stage="GSTRINGS_before";d->error=nc_get(sock,d->name,names);if(d->error) goto done;
 if(names->len!=d->count) {d->error=ESTALE;goto done;}
 d->names=malloc(d->count*NC_NAME);if(!d->names) {d->error=ENOMEM;goto done;}memcpy(d->names,names->data,d->count*NC_NAME);d->names_ok=1;
 stats->cmd=ETHTOOL_GSTATS;stats->n_stats=d->count;
 d->stage="GSTATS";d->error=nc_get(sock,d->name,stats);if(d->error) goto done;
 if(stats->n_stats!=d->count) {d->error=ESTALE;goto done;}
 d->values=malloc(d->count*sizeof *d->values);if(!d->values) {d->error=ENOMEM;goto done;}memcpy(d->values,stats->data,d->count*sizeof *d->values);d->values_ok=1;
 names_after->cmd=ETHTOOL_GSTRINGS;names_after->string_set=ETH_SS_STATS;names_after->len=d->count;
 d->stage="GSTRINGS_after";d->error=nc_get(sock,d->name,names_after);if(d->error) goto done;
 if(names_after->len!=d->count) {d->error=ESTALE;goto done;}
 d->names_after=malloc(d->count*NC_NAME);if(!d->names_after) {d->error=ENOMEM;goto done;}memcpy(d->names_after,names_after->data,d->count*NC_NAME);d->names_after_ok=1;
 d->stage="GDRVINFO_after";after.cmd=ETHTOOL_GDRVINFO;d->error=nc_get(sock,d->name,&after);if(d->error) goto done;
 d->info_after_ok=1;memcpy(d->info_after,&after,sizeof after);d->index_after=if_nametoindex(d->name);
 d->stage="schema_identity_validation";
 if(d->index_before!=d->index_after || memcmp(&before,&after,sizeof before) || memcmp(d->names,d->names_after,d->count*NC_NAME)) {d->error=ESTALE;goto done;}
 d->error=nc_names_valid(d);if(!d->error) d->stage="complete";
 done:free(names);free(names_after);free(stats);close(sock);
}
#elif defined(REMORA_NIC_TEST)
/* Fixture backend is compiled only by tests, absent from installed collectors. */
static void nc_fetch(struct nc_device *d) {
 char path[1024],line[2048];snprintf(path,sizeof path,"%s/class/net/%s/fixture",nc_sys,d->name);
 d->stage="fixture";FILE *in=fopen(path,"r");if(!in) {d->error=errno;return;}
 d->names=calloc(NC_STATS,NC_NAME);d->names_after=calloc(NC_STATS,NC_NAME);d->values=calloc(NC_STATS,sizeof *d->values);
 if(!d->names || !d->names_after || !d->values) {d->error=ENOMEM;fclose(in);return;}
 while(fgets(line,sizeof line,in)) {
  char key[80],val[1024];if(sscanf(line,"%79s %1023s",key,val)!=2) {d->error=EBADMSG;break;}
  if(!strcmp(key,"driver")) {snprintf(d->driver,sizeof d->driver,"%.32s",val);d->info_ok=d->info_after_ok=1;continue;}
  if(!strcmp(key,"error")) {d->error=atoi(val);break;}
  if(!strcmp(key,"signal_wait")) {int fd=open(val,O_WRONLY|O_CREAT|O_EXCL,0600);if(fd>=0) close(fd);for(;;) pause();}
  if(d->count==NC_STATS) {d->error=EOVERFLOW;pn_limit="driver_stat_limit";break;}
  uint64_t v;if(strlen(key)>=NC_NAME || pn_number(val,10,UINT64_MAX,&v)) {d->error=EBADMSG;break;}
  memcpy(d->names+d->count*NC_NAME,key,strlen(key));d->values[d->count++]=v;
 }
 fclose(in);d->names_ok=d->names_after_ok=d->values_ok=1;d->index_before=d->index_after=7;
 memcpy(d->names_after,d->names,d->count*NC_NAME);
 if(getenv("REMORA_NIC_TEST_RACE")) {d->names_after[0]='!';d->error=ESTALE;}
 if(!d->error) d->error=d->count?nc_names_valid(d):EOPNOTSUPP;
}
#else
static void nc_fetch(struct nc_device *d) {d->error=EOPNOTSUPP;d->stage="non_linux";}
#endif
static const char *nc_family(const char *driver) {
 if(!strcmp(driver,"mlx5_core") || !strcmp(driver,"mlx5_core[ib_ipoib]")) return "mlx5";
 if(!strcmp(driver,"ixgbe") || !strcmp(driver,"igb") || !strcmp(driver,"tg3") || !strcmp(driver,"bnxt_en")) return driver;
 return "unsupported_driver";
}
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
static void nc_snapshot(struct nc_device *d) {
 fputs("{\"entity\":",pn_out);pn_quote(d->name);fputs(",\"source\":\"SIOCETHTOOL/ETH_SS_STATS\",\"stage\":",pn_out);pn_quote(d->stage);
 fputs(",\"status\":",pn_out);pn_quote(pn_status(d->error));fprintf(pn_out,",\"errno\":%d,\"count\":%u,\"ifindex_before\":%u,\"ifindex_after\":%u,\"driver_info_hex\":",d->error,d->count,d->index_before,d->index_after);
 nc_hex(d->info,d->info_len,d->info_ok);fputs(",\"driver_info_after_hex\":",pn_out);nc_hex(d->info_after,d->info_len,d->info_after_ok);
 fputs(",\"names_hex\":",pn_out);nc_hex(d->names,d->count*NC_NAME,d->names_ok);fputs(",\"names_after_hex\":",pn_out);nc_hex(d->names_after,d->count*NC_NAME,d->names_after_ok);
 fputs(",\"values\":",pn_out);
 if(!d->values_ok) fputs("null",pn_out);
 else {fputc('[',pn_out);for(unsigned i=0;i<d->count;i++) {if(i) fputc(',',pn_out);fprintf(pn_out,"\"%" PRIu64 "\"",d->values[i]);}fputc(']',pn_out);}
 fputc('}',pn_out);
}
static void nc_field_emit(struct nc_device *d,const char *name,const char *unit,const char *scope) {
 int error=d->error?d->error:ENOENT;char value[32];const char *raw=NULL;
 if(!d->error) for(unsigned i=0;i<d->count;i++) if(!strcmp((char*)d->names+i*NC_NAME,name)) {snprintf(value,sizeof value,"%" PRIu64,d->values[i]);raw=value;error=0;break;}
 pn_emit(d->name,scope,name,"SIOCETHTOOL/ETH_SS_STATS","counter",unit,raw,raw,error);
}
static void nc_records(struct nc_device *d,const char *module) {
 const char *family=nc_family(d->driver);
 if(!strcmp(family,"unsupported_driver")) {
  pn_emit(d->name,"netdev","driver_allowlist","SIOCETHTOOL/GDRVINFO","availability","none",*d->driver?d->driver:NULL,NULL,d->error?d->error:EOPNOTSUPP);return;
 }
 for(unsigned i=0;i<sizeof nc_fields/sizeof *nc_fields;i++) {
  const struct nc_field *f=&nc_fields[i];if(!strcmp(f->driver,family) && !strcmp(f->module,module)) nc_field_emit(d,f->name,f->unit,f->scope);
 }
 if(d->error) return;
 for(unsigned i=0;i<d->count;i++) for(unsigned j=0;j<sizeof nc_patterns/sizeof *nc_patterns;j++) {
  const struct nc_pattern *p=&nc_patterns[j];const char *name=(char*)d->names+i*NC_NAME;size_t a=strlen(p->prefix),z=strlen(p->suffix),len=strlen(name);
  if(strcmp(p->driver,family) || strcmp(p->module,module) || len<=a+z || strncmp(name,p->prefix,a) || strcmp(name+len-z,p->suffix)) continue;
  char token[NC_NAME];size_t digits=len-a-z;memcpy(token,name+a,digits);token[digits]=0;uint64_t index;
  if(pn_number(token,10,p->max_index,&index) || (digits>1 && token[0]=='0')) continue;
  nc_field_emit(d,name,p->unit,p->scope);
 }
}
static int remora_nicstats_main(const char *module,int argc,char **argv) {
 if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--check")) || !pn_name(argv[1])) return 65;
#ifdef REMORA_NIC_TEST
 const char *root=getenv("REMORA_NETWORK_SYSFS_ROOT");if(root) nc_sys=root;
#endif
 struct nc_device devices[NC_IFS];memset(devices,0,sizeof devices);unsigned count=0;
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
  fputs("{\"name\":",pn_out);pn_quote(d->name);fputs(",\"family_evidence\":\"ethtool_driver_identity\",\"driver\":",pn_out);pn_quote(d->driver);
  fputs(",\"driver_family\":",pn_out);pn_quote(nc_family(d->driver));fputs(",\"version\":",pn_out);pn_quote(d->version);fputs(",\"firmware\":",pn_out);pn_quote(d->firmware);fputs(",\"bus_info\":",pn_out);pn_quote(d->bus);
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
 for(unsigned i=0;i<count;i++) {free(devices[i].names);free(devices[i].names_after);free(devices[i].values);}
 free(buffer);close(lfd);close(dir);return status;
}
#endif
