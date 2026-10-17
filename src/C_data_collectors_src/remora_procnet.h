/* Read-only proc network snapshots. Contract: docs/network-proc-resources.md. */
#ifndef REMORA_PROCNET_H
#define REMORA_PROCNET_H
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

#define PN_CAP (1024*1024)
#define PN_ROWS 2048
#define PN_TOKENS 1024
struct pn_source {const char *path; char *raw; size_t len; int error,parse_error;};
struct pn_field {const char *group,*name,*unit; char *value; int error;};
static struct pn_field pn_fields[]={
 {"Tcp","InSegs","segments",NULL,0},{"Tcp","OutSegs","segments",NULL,0},
 {"Tcp","RetransSegs","segments",NULL,0},{"Tcp","InErrs","segments",NULL,0},
 {"Tcp","OutRsts","segments",NULL,0},
 {"Udp","InDatagrams","datagrams",NULL,0},{"Udp","OutDatagrams","datagrams",NULL,0},
 {"Udp","InErrors","datagrams",NULL,0},{"Udp","RcvbufErrors","datagrams",NULL,0},
 {"Udp","SndbufErrors","datagrams",NULL,0},
 {"TcpExt","ListenDrops","events",NULL,0},{"TcpExt","ListenOverflows","events",NULL,0},
 {"TcpExt","TCPBacklogDrop","events",NULL,0},{"TcpExt","TCPTimeouts","events",NULL,0},
 {"TcpExt","TCPSynRetrans","segments",NULL,0}};
static FILE *pn_out;
static unsigned pn_records,pn_readable,pn_entities;
static int pn_bad,pn_first=1;
static const char *pn_limit,*pn_root;
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
  if(d>=base || v>(max-d)/base) return -1;
  v=v*base+d;
 }
 *value=v;return 0;
}
static void pn_read(struct pn_source *s) {
 char path[1024];int n=snprintf(path,sizeof path,"%s/%s",pn_root,s->path);
 if(n<0 || n>=(int)sizeof path) {s->error=ENAMETOOLONG;return;}
 s->raw=calloc(PN_CAP+1,1);if(!s->raw) {s->error=ENOMEM;return;}
 int fd=open(path,O_RDONLY|O_NONBLOCK);if(fd<0) {s->error=errno;return;}
 struct stat st;
 if(fstat(fd,&st) || !S_ISREG(st.st_mode)) {s->error=EBADMSG;close(fd);return;}
 while(s->len<PN_CAP) {
  ssize_t count=read(fd,s->raw+s->len,PN_CAP-s->len);
  if(count<0) {if(errno==EINTR) continue;s->error=errno;break;}
  if(!count) break;
  s->len+=(size_t)count;
 }
 if(s->len==PN_CAP) {s->error=EOVERFLOW;pn_limit="source_byte_limit";}
 if(close(fd) && !s->error) s->error=errno;
 if(!s->error && memchr(s->raw,0,s->len)) s->error=EBADMSG;
 if(!s->error && s->len && s->raw[s->len-1]!='\n') s->error=EBADMSG;
}
static int pn_tokens(char *line,char **tokens) {
 char *save=NULL;int n=0;
 for(char *t=strtok_r(line," \t\r",&save);t;t=strtok_r(NULL," \t\r",&save)) {
  if(n==PN_TOKENS) {pn_limit="line_token_limit";return -1;}
  tokens[n++]=t;
 }
 return n;
}
static void pn_protocol(struct pn_source *s,int extended) {
 if(s->error) return;
 char *copy=strdup(s->raw);if(!copy) {s->parse_error=ENOMEM;return;}
 char *save=NULL,*header=strtok_r(copy,"\n",&save);unsigned pairs=0;int seen[3]={0};
 while(header) {
  char *data=strtok_r(NULL,"\n",&save),*keys[PN_TOKENS],*values[PN_TOKENS];
  if(!data || ++pairs>256) {s->parse_error=EBADMSG;break;}
  int nk=pn_tokens(header,keys),nv=pn_tokens(data,values);
  if(nk<2 || nk!=nv || strcmp(keys[0],values[0]) || keys[0][strlen(keys[0])-1]!=':') {s->parse_error=EBADMSG;break;}
  for(int i=1;i<nk;i++) for(int j=1;j<i;j++) if(!strcmp(keys[i],keys[j])) s->parse_error=EBADMSG;
  int group=!strcmp(keys[0],"Tcp:")?0:!strcmp(keys[0],"Udp:")?1:!strcmp(keys[0],"TcpExt:")?2:-1;
  if(group>=0 && ++seen[group]>1) s->parse_error=EBADMSG;
  if(s->parse_error) break;
  if(group>=0 && (extended==(group==2))) {
   for(unsigned f=0;f<sizeof pn_fields/sizeof *pn_fields;f++) {
    struct pn_field *field=&pn_fields[f];
    if(strncmp(keys[0],field->group,strlen(field->group)) || strlen(keys[0])!=strlen(field->group)+1) continue;
    for(int k=1;k<nk;k++) if(!strcmp(keys[k],field->name)) {
     uint64_t v;field->value=strdup(values[k]);
     if(!field->value) {s->parse_error=ENOMEM;break;}
     if(pn_number(values[k],10,UINT64_MAX,&v)) field->error=EBADMSG;
    }
   }
  }
  header=strtok_r(NULL,"\n",&save);
 }
 if(!pairs && !s->parse_error) s->parse_error=EBADMSG;
 free(copy);
}
static void pn_emit(const char *entity,const char *scope,const char *metric,const char *source,
                    const char *kind,const char *unit,const char *raw,const char *value,int error) {
 if(pn_records>=8192 || ftell(pn_out)>8*1024*1024-65536) {pn_bad=1;pn_limit="sample_record_or_byte_limit";return;}
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
static void pn_softnet(struct pn_source *s,int entities) {
 if(s->error) {
  if(entities) {fputs("{\"name\":\"softnet\",\"family_evidence\":\"source_unavailable\"}",pn_out);pn_entities++;}
  if(!entities) for(unsigned i=0;i<3;i++) pn_emit("softnet","host_cpu",(const char*[]){"processed","dropped","time_squeeze"}[i],s->path,"counter",i==2?"events":"packets",NULL,NULL,s->error);
  return;
 }
 char *copy=strdup(s->raw);if(!copy) {pn_bad=1;s->parse_error=ENOMEM;return;}
 char *save=NULL;unsigned row=0,seen_count=0;uint64_t seen[PN_ROWS];int all_unknown=1;
 for(char *line=strtok_r(copy,"\n",&save);line;line=strtok_r(NULL,"\n",&save)) {
  if(row==PN_ROWS) {pn_bad=1;pn_limit="softnet_row_limit";s->parse_error=EOVERFLOW;break;}
  char *tokens[PN_TOKENS],entity[80],cpu[32],width[32];uint64_t values[15]={0};
  int n=pn_tokens(line,tokens),error=0,known=n==13 || n==15;
  if(n<=0) error=EBADMSG;
  else if(!known) error=EOPNOTSUPP;
  else for(int i=0;i<n;i++) if(strlen(tokens[i])>8 || pn_number(tokens[i],16,UINT32_MAX,&values[i])) error=EBADMSG;
  if(known && !error) {
   all_unknown=0;
   for(unsigned i=0;i<seen_count;i++) if(seen[i]==values[12]) error=EBADMSG;
   if(!error) seen[seen_count++]=values[12];
  }
  if(known && !error) snprintf(entity,sizeof entity,"cpu.%" PRIu64,values[12]);
  else snprintf(entity,sizeof entity,"row.%u",row);
  snprintf(cpu,sizeof cpu,"%" PRIu64,values[12]);snprintf(width,sizeof width,"%d",n);
  if(entities) {
   if(pn_entities++) fputc(',',pn_out);
   fputs("{\"name\":",pn_out);pn_quote(entity);fputs(",\"family_evidence\":\"host_per_cpu_softnet_data\"}",pn_out);
  } else {
   pn_emit(entity,"host_cpu","layout_columns",s->path,"metadata","columns",width,width,n<0?EBADMSG:0);
   pn_emit(entity,"host_cpu","cpu_id",s->path,"metadata","index",known?tokens[12]:NULL,cpu,error);
   for(unsigned i=0;i<3;i++) {
    char decimal[32];snprintf(decimal,sizeof decimal,"%" PRIu64,values[i]);
    pn_emit(entity,"host_cpu",(const char*[]){"processed","dropped","time_squeeze"}[i],s->path,"counter",i==2?"events":"packets",n>(int)i?tokens[i]:NULL,decimal,error);
   }
   if(error && !pn_optional(error)) s->parse_error=error;
  }
  row++;
 }
 if(!entities && all_unknown && row && !s->parse_error) s->parse_error=EOPNOTSUPP;
 if(!entities && !all_unknown && pn_readable<row*3) {pn_bad=1;s->parse_error=EBADMSG;}
 free(copy);
}
static int pn_open(int dir,const char *name,int flags) {
 int fd=openat(dir,name,flags|O_NOFOLLOW|O_NONBLOCK,0600);if(fd<0) return -1;
 struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || st.st_nlink!=1) {close(fd);errno=EPERM;return -1;}return fd;
}
static void pn_time(const char *key,clockid_t clock) {
 struct timespec t;if(clock_gettime(clock,&t)) {pn_bad=1;t.tv_sec=0;t.tv_nsec=0;}
 fprintf(pn_out,"\"%s\":\"%" PRId64 ".%09ld\"",key,(int64_t)t.tv_sec,t.tv_nsec);
}
static int remora_procnet_main(const char *module,int argc,char **argv) {
 if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--check")) || !pn_name(argv[1])) return 65;
 pn_root=getenv("REMORA_NETWORK_PROC_ROOT");if(!pn_root) pn_root="/proc";
 if(pn_root[0]!='/' || strlen(pn_root)>700 || strpbrk(pn_root,"\r\n\t")) return 65;
 int soft=!strcmp(module,"softnet"),status=74;
 struct pn_source sources[2]={{soft?"net/softnet_stat":"net/snmp",NULL,0,0,0},{"net/netstat",NULL,0,0,0}};
 unsigned nsources=soft?1:2;
 if(argc==5) {
  int readable=0,bad=0;
  for(unsigned i=0;i<nsources;i++) {char path[1024];snprintf(path,sizeof path,"%s/%s",pn_root,sources[i].path);int fd=open(path,O_RDONLY|O_NONBLOCK);if(fd>=0) {struct stat st;if(!fstat(fd,&st) && S_ISREG(st.st_mode)) readable=1;else bad=1;close(fd);}else if(!pn_optional(errno)) bad=1;}
  return bad?74:readable?0:69;
 }
 int dir=open(argv[3],O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0) return 74;
 char filename[256],lock[280],pending[280];snprintf(filename,sizeof filename,"%s_%s.jsonl",module,argv[1]);snprintf(lock,sizeof lock,"%s.lock",filename);snprintf(pending,sizeof pending,"%s.pending",filename);
 int lfd=pn_open(dir,lock,O_RDWR|O_CREAT);if(lfd<0) {close(dir);return 74;}
 if(flock(lfd,LOCK_EX|LOCK_NB)) {close(lfd);close(dir);return 75;}
 int pfd=pn_open(dir,pending,O_WRONLY|O_CREAT|O_EXCL),fd=-1;char *buffer=NULL;size_t size=0;
 if(pfd<0) goto done;
 fd=pn_open(dir,filename,O_RDWR|O_APPEND|O_CREAT);if(fd<0) goto done;
 struct stat st;if(fstat(fd,&st)) goto done;
 if(st.st_size) {char last;if(pread(fd,&last,1,st.st_size-1)!=1 || last!='\n') goto done;}
 pn_out=open_memstream(&buffer,&size);if(!pn_out) goto done;
 fputs("{\"schema\":\"remora-network-resources/v1\",\"module\":",pn_out);pn_quote(module);
 fputs(",\"node\":",pn_out);pn_quote(argv[1]);fputs(",\"proc_root\":",pn_out);pn_quote(pn_root);
 struct utsname kernel;fputs(",\"kernel\":",pn_out);pn_quote(uname(&kernel)?"UNKNOWN":kernel.release);
 char ns[256],boot[128]={0};ssize_t n=readlink("/proc/self/ns/net",ns,sizeof ns-1);if(n>=0) ns[n]=0;
 fputs(",\"network_namespace\":",pn_out);pn_quote(n<0?"UNKNOWN":ns);
 int bfd=open("/proc/sys/kernel/random/boot_id",O_RDONLY|O_NONBLOCK);if(bfd>=0) {n=read(bfd,boot,sizeof boot-1);if(n>0) {boot[n]=0;while(n>0 && isspace((unsigned char)boot[n-1])) boot[--n]=0;}close(bfd);}
 fputs(",\"boot_id\":",pn_out);pn_quote(*boot?boot:"UNKNOWN");fputc(',',pn_out);pn_time("wall_start",CLOCK_REALTIME);fputc(',',pn_out);pn_time("mono_start",CLOCK_MONOTONIC);
 for(unsigned i=0;i<nsources;i++) {pn_read(&sources[i]);if(sources[i].error && !pn_optional(sources[i].error)) pn_bad=1;}
 if(!soft) for(unsigned i=0;i<nsources;i++) pn_protocol(&sources[i],i==1);
 fputs(",\"entities\":[",pn_out);
 if(soft) pn_softnet(&sources[0],1);
 else fputs("{\"name\":\"Tcp\",\"family_evidence\":\"proc_header\"},{\"name\":\"Udp\",\"family_evidence\":\"proc_header\"},{\"name\":\"TcpExt\",\"family_evidence\":\"proc_header\"}",pn_out);
 fputs("],\"records\":[",pn_out);
 if(soft) pn_softnet(&sources[0],0);
 else for(unsigned i=0;i<sizeof pn_fields/sizeof *pn_fields;i++) {
  struct pn_field *f=&pn_fields[i];struct pn_source *s=&sources[!strcmp(f->group,"TcpExt")];
  int error=s->error?s->error:s->parse_error?s->parse_error:f->error?f->error:f->value?0:ENOENT;
  pn_emit(f->group,"network_namespace",f->name,s->path,"counter",f->unit,f->value,f->value,error);
 }
 fputs("],\"source_snapshots\":[",pn_out);
 for(unsigned i=0;i<nsources;i++) {
  struct pn_source *s=&sources[i];if(i) fputc(',',pn_out);
  fputs("{\"source\":",pn_out);pn_quote(s->path);fputs(",\"status\":",pn_out);pn_quote(pn_status(s->error));
  fprintf(pn_out,",\"errno\":%d,\"parse_status\":",s->error);pn_quote(pn_status(s->parse_error));
  fprintf(pn_out,",\"parse_errno\":%d,\"raw\":",s->parse_error);pn_quote_n(s->raw,s->len);fputc('}',pn_out);
 }
 fputs("],",pn_out);pn_time("wall_end",CLOCK_REALTIME);fputc(',',pn_out);pn_time("mono_end",CLOCK_MONOTONIC);
 fputs(",\"limitation\":",pn_out);pn_quote(pn_limit);
 fprintf(pn_out,",\"status\":\"%s\",\"readable_primary\":%u,\"record_count\":%u}\n",pn_bad?"PARTIAL":pn_readable?"OK":"UNSUPPORTED",pn_readable,pn_records);
 int bad=ferror(pn_out);if(fclose(pn_out)) bad=1;pn_out=NULL;
 if(bad || size>8*1024*1024) goto done;
 size_t written=0;while(written<size) {ssize_t w=write(fd,buffer+written,size-written);if(w<0 && errno==EINTR) continue;if(w<=0) break;written+=(size_t)w;}
 if(written!=size || fsync(fd)) goto done;
 if(close(fd)) {fd=-1;goto done;}fd=-1;
 if(unlinkat(dir,pending,0)) goto done;
 status=pn_bad?74:pn_readable?0:69;
 done:
 if(pn_out) fclose(pn_out);
 if(fd>=0) close(fd);
 if(pfd>=0) close(pfd);
 free(buffer);for(unsigned i=0;i<nsources;i++) free(sources[i].raw);
 for(unsigned i=0;i<sizeof pn_fields/sizeof *pn_fields;i++) free(pn_fields[i].value);
 close(lfd);close(dir);return status;
}
#endif
