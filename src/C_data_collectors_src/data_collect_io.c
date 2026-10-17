/* MIT. Optional user-space IO providers. Contract: docs/io-resources.md. */
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/statvfs.h>
#include <sys/file.h>
#include <sys/utsname.h>
#include <sys/wait.h>
#include <fcntl.h>
#include <unistd.h>
#include <poll.h>
#include <signal.h>
#include <errno.h>
#include <ctype.h>
#include <inttypes.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <limits.h>

#define IO_VERSION "1"
#define MAX_FILE (2*1024*1024)
#define MAX_OUTPUT (16*1024*1024)
#define MAX_ENT 128
#define MAX_MET 768
#define MAX_PATH 4096
#define MAGIC UINT64_C(0x52494f3030303031)
struct metric { char name[64], unit[24]; uint64_t value; int valid, gauge; const char *reason; };
struct entity {
    char target[MAX_PATH], key[MAX_PATH], source[MAX_PATH], dev[32], root[MAX_PATH], type[64];
    char canonical[MAX_PATH], options[MAX_PATH];
    unsigned mount_id; uint64_t age; int age_valid, identity_known, candidate, identical;
    const char *scope, *status, *reason;
    char *transport, *process_raw, *stat_before, *stat_after;
    char capacity_raw[512];
    unsigned count; struct metric m[MAX_MET];
};
struct mount { unsigned id; char dev[32],root[MAX_PATH],path[MAX_PATH],type[64],source[MAX_PATH],options[MAX_PATH]; };
struct previous_metric { char name[64]; uint64_t value; int valid; };
struct previous_entity { char key[MAX_PATH], target[MAX_PATH]; uint64_t age; int age_valid, known; unsigned count; struct previous_metric m[MAX_MET]; };
struct state { uint64_t magic, checksum, time; unsigned count; int valid; char context[MAX_PATH]; struct previous_entity e[MAX_ENT]; };
static struct entity *entities;
static struct mount mounts[MAX_ENT];
static unsigned count, mount_count;
static const char *status, *reason, *provider;
static char boot[128], ns[128], context[MAX_PATH], node[128], kernel[128];
static char *raw[8]; static const char *raw_path[8]; static unsigned raw_count;
static const char *paths[64], *devices[64], *handles[64];
static unsigned path_count, device_count, handle_count;
static char fixture[MAX_PATH];
static int state_dir=-1, allow_partial=0, discovery=0, legacy=0;
static int pending_fd=-1; static char pending_name[256];
static unsigned timeout_ms=3000;
static volatile sig_atomic_t interrupted;
static struct state *previous;
static uint64_t start_ns, end_ns;
static const char *providers[]={"mounts","capacity","nfs","process","block","tmp"};
static const char *backends[]={"/proc/self/mountinfo","statvfs + /proc/self/mountinfo","/proc/self/mountstats + /proc/self/mountinfo","/proc/PID/stat + /proc/PID/io","/proc/diskstats + /sys/dev/block + /proc/self/mountinfo","statvfs + /proc/self/mountinfo"};
static const char *scopes[]={"mount_namespace","filesystem","node_client_mount","process","node_block_device","filesystem"};

static int fmt(char *s,size_t n,const char *format,...) {
    va_list a;va_start(a,format);int r=vsnprintf(s,n,format,a);va_end(a);
    if(r<0 || (size_t)r>=n) {errno=EOVERFLOW;return -1;} return 0;
}
static uint64_t now(clockid_t clock) {
#ifdef REMORA_IO_TESTING
    const char *fake=getenv(clock==CLOCK_MONOTONIC?"IO_TEST_MONO_NS":"IO_TEST_WALL_NS");if(fake) return strtoull(fake,NULL,10);
#endif
    struct timespec t;if(clock_gettime(clock,&t)) return 0;return (uint64_t)t.tv_sec*UINT64_C(1000000000)+(uint64_t)t.tv_nsec;
}
static void quoted(FILE *f,const char *s) {
    if(!s) {fputs("null",f);return;} fputc('"',f);
    for(const unsigned char *p=(const unsigned char*)s;*p;p++) {
        if(*p=='"' || *p=='\\') {fputc('\\',f);fputc(*p,f);}
        else if(*p<32 || *p>=127) fprintf(f,"\\u%04x",*p);
        else fputc(*p,f);
    } fputc('"',f);
}
static int uint_value(const char *s,uint64_t *v) {
    if(!s || !isdigit((unsigned char)*s)) return -1;
    uint64_t x=0;for(;*s;s++) {unsigned d=(unsigned)(*s-'0');if(d>9 || x>(UINT64_MAX-d)/10) return -1;x=x*10+d;}
    *v=x;return 0;
}
static int safe_name(const char *s) {
    if(!s || !*s || strlen(s)>120 || !strcmp(s,".") || !strcmp(s,"..")) return 0;
    for(;*s;s++) if(!isalnum((unsigned char)*s) && !strchr("_.-",*s)) return 0;
    return 1;
}
static const char *error_reason(int e) {return e==EACCES || e==EPERM?"PERMISSION":e==ENOENT || e==ESRCH?"MISSING":e==EOVERFLOW?"LIMIT":e==EBADMSG?"PARSE":"READ_ERROR";}
static void fail(const char *why) {status="PARTIAL";if(!reason) reason=why;}
static int rooted(const char *path,char out[MAX_PATH]) {return fmt(out,MAX_PATH,"%s%s",fixture,path);}
/* Never block opening a FIFO; proc/sysfs report regular-file type despite size 0. */
static char *text(const char *path) {
#ifdef REMORA_IO_TESTING
    const char *fault=getenv("IO_TEST_FAULT_PATH");
    if(fault && !strcmp(path,fault)) {
        const char *error=getenv("IO_TEST_ERRNO");if(error) {errno=atoi(error);return NULL;}
        const char *ready=getenv("IO_TEST_READY");
        if(ready) {FILE *f=fopen(ready,"w");if(f) {fprintf(f,"%ld",(long)getpid());fclose(f);}for(;;) pause();}
        const char *race=getenv("IO_TEST_RACE_STAT");if(race) {char dest[MAX_PATH];rooted(race,dest);FILE *f=fopen(dest,"w");if(f) {fputs("4242 (changed) S 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 78\n",f);fclose(f);}}
    }
#endif
    char full[MAX_PATH];if(rooted(path,full)) return NULL;
    int fd=open(full,O_RDONLY|O_NONBLOCK);if(fd<0) return NULL;
    struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode)) {close(fd);errno=EBADMSG;return NULL;}
    char *s=malloc(MAX_FILE+1);if(!s) {close(fd);return NULL;}
    size_t size=0;int saved=0;
    while(size<MAX_FILE) {ssize_t n=read(fd,s+size,MAX_FILE-size);if(n<0) {if(errno==EINTR) continue;saved=errno;break;}if(!n) break;size+=(size_t)n;}
    if(size==MAX_FILE && !saved) saved=EOVERFLOW;
    if(close(fd) && !saved) saved=errno;
    if(saved) {free(s);errno=saved;return NULL;}
    if(memchr(s,0,size)) {free(s);errno=EBADMSG;return NULL;}s[size]=0;return s;
}
static char *read_raw(const char *path) {
    char *s=text(path);if(!s) {fail(error_reason(errno));return NULL;}
    if(!*s || s[strlen(s)-1]!='\n') {free(s);fail("INCOMPLETE_SOURCE");return NULL;}
    if(raw_count==8) {free(s);fail("LIMIT");return NULL;}
    raw_path[raw_count]=path;raw[raw_count++]=s;return s;
}
static void trim(char *s) {size_t n=strlen(s);while(n && isspace((unsigned char)s[n-1])) s[--n]=0;}
static int unescape(char *s) {
    char *d=s;for(char *p=s;*p;p++) {
        if(*p=='\\') {
            if(!p[1] || !p[2] || !p[3] || p[1]<'0' || p[1]>'7' || p[2]<'0' || p[2]>'7' || p[3]<'0' || p[3]>'7') return -1;
            unsigned v=(p[1]-'0')*64+(p[2]-'0')*8+(p[3]-'0');if(!v || v>255) return -1;*d++=(char)v;p+=3;
        } else *d++=*p;
    }*d=0;return 0;
}
static unsigned tokens(char *s,char **out,unsigned cap) {
    unsigned n=0;char *save=NULL;
    for(char *t=strtok_r(s," \t\r",&save);t;t=strtok_r(NULL," \t\r",&save)) {if(n==cap) return cap+1;out[n++]=t;}return n;
}
static int parse_mounts(void) {
    char *source=read_raw("/proc/self/mountinfo");if(!source) return -1;
    char *copy=strdup(source), *save=NULL;if(!copy) return -1;
    int bad=0;
    for(char *line=strtok_r(copy,"\n",&save);line;line=strtok_r(NULL,"\n",&save)) {
        char *t[128];unsigned n=tokens(line,t,128),sep=6;uint64_t id,parent,major,minor;
        if(n>128) {bad=1;break;}while(sep<n && strcmp(t[sep],"-")) sep++;
        if(n<10 || sep+4!=n || uint_value(t[0],&id) || id>UINT_MAX || uint_value(t[1],&parent)) {bad=1;break;}
        char *colon=strchr(t[2],':');if(!colon) {bad=1;break;}*colon=0;
        if(uint_value(t[2],&major) || uint_value(colon+1,&minor) || major>UINT_MAX || minor>UINT_MAX) {bad=1;break;}*colon=':';
        if(mount_count==MAX_ENT) {fail("LIMIT");bad=1;break;}
        for(unsigned i=0;i<mount_count;i++) if(mounts[i].id==(unsigned)id) bad=1;
        if(bad) break;
        struct mount *m=&mounts[mount_count++];m->id=(unsigned)id;
        if(fmt(m->dev,sizeof m->dev,"%s",t[2]) || fmt(m->root,sizeof m->root,"%s",t[3]) || fmt(m->path,sizeof m->path,"%s",t[4]) || fmt(m->type,sizeof m->type,"%s",t[sep+1]) || fmt(m->source,sizeof m->source,"%s",t[sep+2]) || fmt(m->options,sizeof m->options,"%s,%s",t[5],t[sep+3]) || unescape(m->root) || unescape(m->path) || unescape(m->source) || m->path[0]!='/') {bad=1;break;}
    }
    free(copy);if(bad || !mount_count) {mount_count=0;fail("PARSE");return -1;}return 0;
}
static struct mount *mount_for(const char *path) {
    struct mount *best=NULL;size_t length=0;
    for(unsigned i=0;i<mount_count;i++) {
        struct mount *m=&mounts[i];size_t n=strlen(m->path);
        if(!strncmp(path,m->path,n) && (n==1 || !path[n] || path[n]=='/') && n>=length) {best=m;length=n;}
    }return best;
}
static struct entity *add(const char *target,const char *scope) {
    if(count==MAX_ENT) {fail("LIMIT");return NULL;}
    struct entity *e=&entities[count++];e->scope=scope;e->status="PASS";
    if(fmt(e->target,sizeof e->target,"%s",target)) {e->status="INVALID";e->reason="LIMIT";fail("LIMIT");}return e;
}
static void entity_fail(struct entity *e,const char *why) {e->status="MISSING";e->reason=why;fail(why);}
static void identity_mount(struct entity *e,const struct mount *m) {
    e->mount_id=m->id;fmt(e->options,sizeof e->options,"%s",m->options);
    fmt(e->dev,sizeof e->dev,"%s",m->dev);fmt(e->root,sizeof e->root,"%s",m->root);fmt(e->source,sizeof e->source,"%s",m->source);fmt(e->type,sizeof e->type,"%s",m->type);
    e->identity_known=*boot && *ns;
    if(fmt(e->key,sizeof e->key,"%s|%u|%s|%s|%s|%s",ns,m->id,m->dev,m->root,m->source,m->type)) {e->identity_known=0;entity_fail(e,"LIMIT");}
}
static struct metric *metric(struct entity *e,const char *name,const char *unit,int gauge,uint64_t v,const char *why) {
    if(e->count==MAX_MET) {entity_fail(e,"LIMIT");return NULL;}
    for(unsigned i=0;i<e->count;i++) if(!strcmp(e->m[i].name,name)) {entity_fail(e,"DUPLICATE_METRIC");return NULL;}
    struct metric *m=&e->m[e->count++];
    if(fmt(m->name,sizeof m->name,"%s",name) || fmt(m->unit,sizeof m->unit,"%s",unit)) {entity_fail(e,"LIMIT");return NULL;}
    m->gauge=gauge;m->value=v;m->valid=!why;m->reason=why;return m;
}
static void capacity(struct entity *e,const char *canonical) {
    static const char *names[]={"total_bytes","free_bytes","available_bytes","total_inodes","free_inodes","available_inodes"};
    char full[MAX_PATH];struct statvfs v;int good=!rooted(canonical,full) && !statvfs(full,&v);const char *why=good?NULL:error_reason(errno);
    if(!good) entity_fail(e,why);
    uint64_t a[6]={0};
    if(good) fmt(e->capacity_raw,sizeof e->capacity_raw,"f_blocks=%" PRIu64 " f_frsize=%" PRIu64 " f_bfree=%" PRIu64 " f_bavail=%" PRIu64 " f_files=%" PRIu64 " f_ffree=%" PRIu64 " f_favail=%" PRIu64,(uint64_t)v.f_blocks,(uint64_t)v.f_frsize,(uint64_t)v.f_bfree,(uint64_t)v.f_bavail,(uint64_t)v.f_files,(uint64_t)v.f_ffree,(uint64_t)v.f_favail);
    if(good) {a[0]=v.f_blocks;a[1]=v.f_bfree;a[2]=v.f_bavail;a[3]=v.f_files;a[4]=v.f_ffree;a[5]=v.f_favail;}
    for(unsigned i=0;i<6;i++) {
        const char *missing=why;uint64_t value=a[i];
        if(good && i<3) {if(!v.f_blocks || !v.f_frsize) missing="UNSUPPORTED_CAPACITY";else if(a[i]>v.f_blocks || a[i]>UINT64_MAX/v.f_frsize) missing="INVALID_CAPACITY";else value=a[i]*v.f_frsize;}
        if(good && i>=3 && (!v.f_files || a[i]>v.f_files)) missing="UNSUPPORTED_INODES";
        metric(e,names[i],i<3?"bytes":"inodes",1,value,missing);
        if(missing && good) {e->status="PARTIAL";e->reason=missing;fail(missing);}
    }
}
static int canonical_path(const char *path,char out[MAX_PATH]) {
    char full[MAX_PATH],resolved[MAX_PATH];if(rooted(path,full) || !realpath(full,resolved)) return -1;
    size_t n=strlen(fixture);
    if(n && (strncmp(resolved,fixture,n) || (resolved[n] && resolved[n]!='/'))) {errno=EPERM;return -1;}
    return fmt(out,MAX_PATH,"%s",resolved[n]?resolved+n:"/");
}
static void mounts_sample(void) {
    for(unsigned i=0;i<mount_count;i++) {struct entity *e=add(mounts[i].path,"mount_namespace");if(!e) return;identity_mount(e,&mounts[i]);}
}
static void paths_sample(int tmp) {
    const char *defaults[]={"/tmp","/dev/shm"};unsigned n=path_count?path_count:tmp?2:1;
    for(unsigned i=0;i<n;i++) {
        const char *path=path_count?paths[i]:defaults[i];struct entity *e=add(path,"filesystem");if(!e) return;
        char canonical[MAX_PATH];if(canonical_path(path,canonical)) {entity_fail(e,error_reason(errno));if(!discovery) capacity(e,path);continue;}
        fmt(e->canonical,sizeof e->canonical,"%s",canonical);
        struct mount *m=mount_for(canonical);if(!m) {entity_fail(e,"MOUNT_UNKNOWN");continue;}identity_mount(e,m);
        /* Preserve the requested alias and the resolved mount mapping separately. */
        if(!discovery) capacity(e,canonical);
    }
}
static int values(char **t,unsigned n,uint64_t *a,unsigned minimum,unsigned maximum) {
    if(n<minimum || n>maximum) return -1;
    for(unsigned i=0;i<n;i++) {
        if(uint_value(t[i],&a[i])) return -1;
    }
    return 0;
}
static void nfs_sample(void) {
    char *source=read_raw("/proc/self/mountstats");if(!source) return;
    for(unsigned i=0;i<mount_count;i++) if(!strcmp(mounts[i].type,"nfs") || !strcmp(mounts[i].type,"nfs4")) {
        struct entity *e=add(mounts[i].path,"node_client_mount");if(!e) return;identity_mount(e,&mounts[i]);e->status="MISSING";e->reason="NO_MOUNTSTATS";
    }
    if(!count) {status="SKIP";reason="NO_NFS_MOUNTS";return;}
    char *copy=strdup(source),*save=NULL;struct entity *e=NULL;unsigned seen[MAX_ENT]={0},bytes[MAX_ENT]={0};
    for(char *line=strtok_r(copy,"\n",&save);line;line=strtok_r(NULL,"\n",&save)) {
        while(isspace((unsigned char)*line)) line++;
        if(!strncmp(line,"device ",7)) {
            e=NULL;char *t[16];unsigned n=tokens(line,t,16);
            if(n<8 || n>16 || strcmp(t[2],"mounted") || strcmp(t[3],"on") || strcmp(t[5],"with") || strcmp(t[6],"fstype")) {fail("PARSE");continue;}
            if(unescape(t[1]) || unescape(t[4])) {fail("PARSE");continue;}
            for(unsigned i=0;i<count;i++) if(!strcmp(entities[i].target,t[4]) && !strcmp(entities[i].source,t[1]) && !strcmp(entities[i].type,t[7])) {
                e=&entities[i];if(seen[i]++) {entity_fail(e,"DUPLICATE_MOUNTSTATS");e=NULL;break;}
                if(n!=9 || (strcmp(t[8],"statvers=1.0") && strcmp(t[8],"statvers=1.1"))) {entity_fail(e,"UNKNOWN_STATVERS");e=NULL;break;}
                e->status="PASS";e->reason=NULL;break;
            }continue;
        }
        if(!e) continue;
        if(!strncmp(line,"xprt:",5)) {
            size_t old=e->transport?strlen(e->transport):0,len=strlen(line);
            if(old+len>MAX_FILE) {entity_fail(e,"LIMIT");continue;}
            char *p=realloc(e->transport,old+len+2);if(!p) {entity_fail(e,"MEMORY");continue;}e->transport=p;memcpy(p+old,line,len);p[old+len]='\n';p[old+len+1]=0;continue;
        }
        if(!strncmp(line,"age:",4)) {
            char *p=line+4;while(isspace((unsigned char)*p)) p++;trim(p);
            if(e->age_valid || uint_value(p,&e->age)) entity_fail(e,"INVALID_AGE");else e->age_valid=1;continue;
        }
        char *colon=strchr(line,':');if(!colon) continue;*colon=0;
        int isbytes=!strcmp(line,"bytes");
        if(!isbytes) {int uppercase=*line!=0;for(char *p=line;*p;p++) if(!isupper((unsigned char)*p) && *p!='_') uppercase=0;if(!uppercase) continue;}
        char *t[20];unsigned n=tokens(colon+1,t,20);uint64_t a[20];
        if(n>20 || values(t,n,a,isbytes?8:8,isbytes?8:9)) {entity_fail(e,"UNKNOWN_COUNTER_LAYOUT");continue;}
        if(isbytes) {
            static const char *names[]={"normal_read_bytes","normal_write_bytes","direct_read_bytes","direct_write_bytes","server_read_bytes","server_write_bytes","read_pages","write_pages"};
            bytes[e-entities]++;
            for(unsigned i=0;i<8;i++) metric(e,names[i],i<6?"bytes":"pages",0,a[i],NULL);
        } else {
            static const char *names[]={"ops","transmissions","major_timeouts","sent_bytes","received_bytes","queue_ms","rtt_ms","execute_ms","errors"};
            for(unsigned i=0;i<9;i++) {char name[64];if(fmt(name,sizeof name,"%s.%s",line,names[i])) {entity_fail(e,"LIMIT");break;}metric(e,name,i<3 || i==8?"count":i<5?"bytes":"ms",0,i<n?a[i]:0,i<n?NULL:"OPTIONAL_FIELD_ABSENT");}
        }
    }free(copy);
    for(unsigned i=0;i<count;i++) {
        e=&entities[i];if(strcmp(e->status,"PASS")) fail(e->reason);
        if(!discovery && (bytes[i]!=1 || !e->age_valid || e->count<=8)) entity_fail(e,"INCOMPLETE_MOUNTSTATS");
        for(unsigned j=0;j<count;j++) if(i!=j && !strcmp(e->dev,entities[j].dev)) {
            e->candidate=1;int equal=e->count==entities[j].count && e->count>0;
            for(unsigned k=0;equal && k<e->count;k++) if(strcmp(e->m[k].name,entities[j].m[k].name) || e->m[k].valid!=entities[j].m[k].valid || (e->m[k].valid && e->m[k].value!=entities[j].m[k].value)) equal=0;
            if(equal) e->identical=1;
        }
    }
}
static int process_identity(unsigned pid,uint64_t *ticks,uid_t *uid,char **raw_stat) {
    char path[128],full[MAX_PATH];struct stat st;
    fmt(path,sizeof path,"/proc/%u",pid);if(rooted(path,full) || stat(full,&st)) return -1;*uid=st.st_uid;
    fmt(path,sizeof path,"/proc/%u/stat",pid);char *s=text(path);if(!s) return -1;
    *raw_stat=strdup(s);
    char *end=strrchr(s,')');char *begin=strchr(s,'(');uint64_t parsed_pid;int ok=0;
    if(begin && end && begin<end && begin>s && begin[-1]==' ') {
        begin[-1]=0;char *t[64];trim(end+1);unsigned n=tokens(end+1,t,64);
        if(!uint_value(s,&parsed_pid) && parsed_pid==pid && n>=20 && n<=64 && !uint_value(t[19],ticks)) ok=1;
    }free(s);if(!ok) {errno=EBADMSG;return -1;}return 0;
}
static void process_sample(void) {
    if(!handle_count) {status="SKIP";reason="NO_EXPLICIT_HANDLES";return;}
    static const char *names[]={"rchar","wchar","syscr","syscw","read_bytes","write_bytes","cancelled_write_bytes"};
    for(unsigned i=0;i<handle_count;i++) {
        struct entity *e=add(handles[i],"process");if(!e) return;
        char handle[128];fmt(handle,sizeof handle,"%s",handles[i]);char *colon=strchr(handle,':');uint64_t p=0,wanted=0,first=0,last=0;uid_t uid=0,again=0;
        if(!colon) {entity_fail(e,"INVALID_HANDLE");continue;}*colon=0;
        if(uint_value(handle,&p) || !p || p>INT_MAX || uint_value(colon+1,&wanted)) {entity_fail(e,"INVALID_HANDLE");continue;}
        const char *why=NULL;
        if(process_identity((unsigned)p,&first,&uid,&e->stat_before)) why=error_reason(errno);
        else if(uid!=geteuid()) why="PERMISSION";
        else if(first!=wanted) why="IDENTITY_CHANGED";
        fmt(e->key,sizeof e->key,"%" PRIu64 ":%" PRIu64 ":%lu",p,wanted,(unsigned long)uid);e->identity_known=*boot && !why;
        uint64_t a[7]={0};int valid[7]={0};
        if(!why && !discovery) {
            char path[128];fmt(path,sizeof path,"/proc/%" PRIu64 "/io",p);char *s=text(path);
            if(!s) why=error_reason(errno);
            else {
                e->process_raw=strdup(s);
                char *save=NULL;for(char *line=strtok_r(s,"\n",&save);line;line=strtok_r(NULL,"\n",&save)) {
                    char *c=strchr(line,':');if(!c) {why="PARSE";continue;}*c++=0;while(isspace((unsigned char)*c)) c++;trim(c);
                    for(unsigned j=0;j<7;j++) if(!strcmp(line,names[j])) {if(valid[j] || uint_value(c,&a[j])) why="PARSE";else valid[j]=1;}
                }free(s);
            }
            /* Identity and UID are rechecked after IO; a PID race invalidates every field. */
            if(process_identity((unsigned)p,&last,&again,&e->stat_after)) why=error_reason(errno);
            else if(last!=first || again!=uid) why="IDENTITY_CHANGED";
        }
        if(why) {entity_fail(e,why);e->identity_known=0;}
        if(!discovery) for(unsigned j=0;j<7;j++) {const char *missing=why?why:valid[j]?NULL:"FIELD_ABSENT";metric(e,names[j],j==2 || j==3?"count":"bytes",0,a[j],missing);if(missing && !why) {e->status="PARTIAL";fail(missing);}}
    }
}
static int device_name(const char *dev) {
    char copy[64];if(fmt(copy,sizeof copy,"%s",dev)) return 0;char *colon=strchr(copy,':');uint64_t a,b;
    if(!colon) return 0;
    *colon=0;
    return !uint_value(copy,&a) && !uint_value(colon+1,&b) && a<=UINT_MAX && b<=UINT_MAX;
}
static void block_sample(void) {
    char *source=read_raw("/proc/diskstats");if(!source) return;
    const char *defaults[]={"/tmp"};unsigned n=device_count?device_count:path_count?path_count:1;
    for(unsigned i=0;i<n;i++) {
        const char *target=device_count?devices[i]:path_count?paths[i]:defaults[i];struct entity *e=add(target,"node_block_device");if(!e) return;
        if(device_count) fmt(e->dev,sizeof e->dev,"%s",target);
        else {
            char canonical[MAX_PATH];if(canonical_path(target,canonical)) {entity_fail(e,error_reason(errno));continue;}
            fmt(e->canonical,sizeof e->canonical,"%s",canonical);
        struct mount *m=mount_for(canonical);if(!m) {entity_fail(e,"MOUNT_UNKNOWN");continue;}
            identity_mount(e,m);
            if(!strcmp(m->type,"nfs") || !strcmp(m->type,"nfs4") || !strcmp(m->type,"tmpfs") || !strcmp(m->type,"lustre")) {e->status="SKIP";e->reason="NOT_LOCAL_BLOCK";continue;}
        }
        char path[128],full[MAX_PATH],generation[96]="";struct stat st;
        fmt(path,sizeof path,"/sys/dev/block/%s",e->dev);
        int known=!rooted(path,full) && !stat(full,&st);int identity_error=known?0:errno;fmt(path,sizeof path,"/sys/dev/block/%s/diskseq",e->dev);char *seq=text(path);uint64_t diskseq;
        if(seq) {trim(seq);if(!uint_value(seq,&diskseq)) fmt(generation,sizeof generation,"%" PRIu64,diskseq);free(seq);}
        e->identity_known=known && *generation && *boot;
        fmt(e->key,sizeof e->key,"%s|%s|%" PRIu64 ":%" PRIu64,e->dev,generation,known?(uint64_t)st.st_dev:0,known?(uint64_t)st.st_ino:0);
        char *copy=strdup(source),*save=NULL;unsigned matches=0;
        for(char *line=strtok_r(copy,"\n",&save);line;line=strtok_r(NULL,"\n",&save)) {
            char *t[32];unsigned width=tokens(line,t,32);uint64_t major,minor,a[17];
            if(width<3 || width>32 || uint_value(t[0],&major) || uint_value(t[1],&minor)) {entity_fail(e,"PARSE");continue;}
            char dev[32];fmt(dev,sizeof dev,"%" PRIu64 ":%" PRIu64,major,minor);if(strcmp(e->dev,dev)) continue;
            matches++;
            if((width!=14 && width!=18 && width!=20) || values(t+3,width-3,a,11,17)) {entity_fail(e,"UNKNOWN_DISKSTATS_LAYOUT");continue;}
            fmt(e->source,sizeof e->source,"%s",t[2]);
            static const char *names[]={"read_ios","read_merges","read_sectors_512","read_ms","write_ios","write_merges","write_sectors_512","write_ms","in_flight","io_ms","weighted_io_ms","discard_ios","discard_merges","discard_sectors_512","discard_ms","flush_ios","flush_ms"};
            for(unsigned j=0;j<17;j++) metric(e,names[j],j==2 || j==6 || j==13?"sectors_512_bytes":j==3 || j==7 || j==9 || j==10 || j==14 || j==16?"ms":"count",j==8,j<width-3?a[j]:0,j<width-3?NULL:"OPTIONAL_FIELD_ABSENT");
        }free(copy);
        if(matches!=1) {
            if(!matches && !device_count && !known && identity_error==ENOENT && !strcmp(e->status,"PASS")) {
                e->status="SKIP";e->reason="NO_LOCAL_BLOCK_MAPPING";
            } else entity_fail(e,matches?"DUPLICATE_DEVICE":"DEVICE_ABSENT");
        }
    }
    for(unsigned i=0;i<count;i++) for(unsigned j=0;j<count;j++) if(i!=j && *entities[i].dev && !strcmp(entities[i].dev,entities[j].dev)) entities[i].candidate=1;
}
static int owned_file(const char *name,int flags) {
    int fd=openat(state_dir,name,flags|O_NOFOLLOW|O_NONBLOCK,0600);if(fd<0) return -1;
    struct stat st;if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_uid!=geteuid() || st.st_nlink!=1) {close(fd);errno=EPERM;return -1;}return fd;
}
static void state_name(char out[256]) {fmt(out,256,".io_%s_%s.state",node,provider);}
static size_t state_size(const struct state *s) {return (size_t)((const char*)&s->e[s->count]-(const char*)s);}
static uint64_t checksum(struct state *s) {
    uint64_t old=s->checksum,h=UINT64_C(14695981039346656037);s->checksum=0;
    const unsigned char *p=(const unsigned char*)s;for(size_t i=0;i<state_size(s);i++) {h^=p[i];h*=UINT64_C(1099511628211);}s->checksum=old;return h;
}
static void load_state(void) {
    previous=calloc(1,sizeof *previous);if(!previous || state_dir<0) return;
    char name[256];state_name(name);int fd=owned_file(name,O_RDONLY);if(fd<0) return;
    struct stat st;size_t used=0;
    if(!fstat(fd,&st) && st.st_size>0 && st.st_size<=(off_t)sizeof *previous) {
        while(used<(size_t)st.st_size) {ssize_t n=read(fd,(char*)previous+used,(size_t)st.st_size-used);if(n<=0) break;used+=(size_t)n;}
    }close(fd);
    int good=previous->magic==MAGIC && previous->count<=MAX_ENT && used==state_size(previous) && previous->checksum==checksum(previous) && memchr(previous->context,0,sizeof previous->context);
    for(unsigned i=0;good && i<previous->count;i++) {
        struct previous_entity *e=&previous->e[i];good=e->count<=MAX_MET && memchr(e->key,0,sizeof e->key) && memchr(e->target,0,sizeof e->target);
        for(unsigned j=0;good && j<e->count;j++) good=memchr(e->m[j].name,0,sizeof e->m[j].name)!=NULL;
    }
    if(!good) memset(previous,0,sizeof *previous);
}
static int store_state(void) {
    if(state_dir<0 || (strcmp(provider,"nfs") && strcmp(provider,"process") && strcmp(provider,"block"))) return 0;
    struct state *s=calloc(1,sizeof *s);if(!s) return -1;
    s->magic=MAGIC;s->count=count;s->time=end_ns;s->valid=!strcmp(status,"PASS");fmt(s->context,sizeof s->context,"%s",context);
    for(unsigned i=0;i<count;i++) {
        struct entity *e=&entities[i];struct previous_entity *p=&s->e[i];fmt(p->key,sizeof p->key,"%s",e->key);fmt(p->target,sizeof p->target,"%s",e->target);
        p->age=e->age;p->age_valid=e->age_valid;p->known=e->identity_known;p->count=e->count;
        for(unsigned j=0;j<e->count;j++) {fmt(p->m[j].name,sizeof p->m[j].name,"%s",e->m[j].name);p->m[j].value=e->m[j].value;p->m[j].valid=e->m[j].valid;}
    }
    s->checksum=checksum(s);char name[256];state_name(name);
    struct stat st;if(!fstatat(state_dir,name,&st,AT_SYMLINK_NOFOLLOW) && (!S_ISREG(st.st_mode) || st.st_uid!=geteuid() || st.st_nlink!=1)) {free(s);errno=EPERM;return -1;}
    if(pending_fd<0) {free(s);return -1;}
    size_t used=0,len=state_size(s);while(used<len) {ssize_t n=write(pending_fd,(char*)s+used,len-used);if(n<0 && errno==EINTR) continue;if(n<=0) break;used+=(size_t)n;}
    int r=used==len?fsync(pending_fd):-1;
    if(r) ftruncate(pending_fd,0);
    free(s);return r;
}

static void invalidate_state(void) {
    if(state_dir<0) return;
    char name[256];state_name(name);
    /* Only this locked node/provider state is invalidated; no wildcard cleanup. */
    int fd=owned_file(name,O_WRONLY);if(fd>=0) {ftruncate(fd,0);close(fd);}
}
static void interval(FILE *f,const struct entity *e,const struct metric *m) {
    const char *why=NULL;uint64_t delta=0,elapsed=0;struct previous_entity *p=NULL;struct previous_metric *old=NULL;
    if(!m->valid || strcmp(status,"PASS") || strcmp(e->status,"PASS")) why="GAP";
    else if(!e->identity_known || !*boot || (!strcmp(provider,"nfs") && !e->age_valid)) why="IDENTITY_UNKNOWN";
    else if(!previous || !previous->valid) why="GAP";
    else if(strcmp(previous->context,context)) why="IDENTITY_CHANGED";
    else {
        for(unsigned i=0;i<previous->count;i++) if(!strcmp(previous->e[i].target,e->target)) {p=&previous->e[i];break;}
        if(!p) why="GAP";
        else if(!p->known || strcmp(p->key,e->key) || (e->age_valid && (!p->age_valid || e->age<p->age))) why="IDENTITY_CHANGED";
        else if(end_ns<=previous->time) why="INVALID_INTERVAL";
        else {
            elapsed=end_ns-previous->time;
            for(unsigned i=0;i<p->count;i++) if(!strcmp(p->m[i].name,m->name)) {old=&p->m[i];break;}
            if(!old || !old->valid) why="GAP";
            else if(m->value<old->value) why="RESET";
            else delta=m->value-old->value;
        }
    }
    fputs(",\"interval\":{\"status\":",f);quoted(f,why?why:"PASS");fputs(",\"delta\":",f);
    if(why) fputs("null,\"elapsed_ns\":null,\"baseline_end_ns\":null,\"per_second\":null}",f);
    else fprintf(f,"%" PRIu64 ",\"elapsed_ns\":%" PRIu64 ",\"baseline_end_ns\":%" PRIu64 ",\"per_second\":%.12Lg}",delta,elapsed,previous->time,(long double)delta*1000000000.0L/(long double)elapsed);
}
static void emit(FILE *f) {
    fprintf(f,"{\"schema\":\"remora-resource-sample/v1\",\"module_version\":\"%s\",\"module\":",IO_VERSION);quoted(f,provider);
    fputs(",\"operation\":",f);quoted(f,discovery?"discover":"sample");fputs(",\"status\":",f);quoted(f,status);fputs(",\"reason\":",f);quoted(f,reason);
    fprintf(f,",\"wall_time_ns\":%" PRIu64 ",\"sample_start_ns\":%" PRIu64 ",\"sample_end_ns\":%" PRIu64,now(CLOCK_REALTIME),start_ns,end_ns);
    fputs(",\"context\":{\"node\":",f);quoted(f,node);fputs(",\"kernel\":",f);quoted(f,kernel);fputs(",\"boot_id\":",f);quoted(f,*boot?boot:NULL);fputs(",\"mount_namespace\":",f);quoted(f,*ns?ns:NULL);
    fputs(",\"job_attribution\":false,\"scheduler_provenance\":null,\"source_revision\":null,\"build_manifest\":",f);
    const char *bin=getenv("REMORA_BIN");char manifest[MAX_PATH];if(bin && !fmt(manifest,sizeof manifest,"%s/../build-manifest.json",bin)) quoted(f,manifest);else fputs("null",f);
    fprintf(f,",\"fixture\":%s},\"qualification\":{\"correctness\":\"NOT_QUALIFIED\",\"overhead\":\"NOT_QUALIFIED\"},\"entities\":[",*fixture?"true":"false");
    for(unsigned i=0;i<count;i++) {
        struct entity *e=&entities[i];if(i) fputc(',',f);fputs("{\"target\":",f);quoted(f,e->target);fputs(",\"scope\":",f);quoted(f,e->scope);fputs(",\"status\":",f);quoted(f,e->status);fputs(",\"reason\":",f);quoted(f,e->reason);
        fputs(",\"canonical_path\":",f);quoted(f,e->canonical);fputs(",\"configuration\":",f);quoted(f,e->options);
        fputs(",\"identity\":{\"key\":",f);quoted(f,e->key);fprintf(f,",\"known\":%s,\"mount_id\":%u,\"device\":",e->identity_known?"true":"false",e->mount_id);quoted(f,e->dev);
        fputs(",\"root\":",f);quoted(f,e->root);fputs(",\"source\":",f);quoted(f,e->source);fputs(",\"fstype\":",f);quoted(f,e->type);fputc('}',f);
        fprintf(f,",\"aggregation\":\"forbidden\",\"shared_domain_candidate\":%s,\"identical_snapshot_counters\":%s,\"mount_age_seconds\":",e->candidate?"true":"false",e->identical?"true":"false");
        if(e->age_valid) fprintf(f,"%" PRIu64,e->age);else fputs("null",f);
        fputs(",\"quota_claim\":false,\"job_private_claim\":false,\"storage_kind\":",f);quoted(f,!strcmp(e->type,"tmpfs")?"memory_backed":!strcmp(e->type,"nfs") || !strcmp(e->type,"nfs4")?"remote":"unspecified");
        fputs(",\"process_io_raw\":",f);quoted(f,e->process_raw);fputs(",\"process_stat_before_raw\":",f);quoted(f,e->stat_before);fputs(",\"process_stat_after_raw\":",f);quoted(f,e->stat_after);fputs(",\"statvfs_raw\":",f);quoted(f,*e->capacity_raw?e->capacity_raw:NULL);
        fputs(",\"transports_raw\":",f);quoted(f,e->transport);fputs(",\"metrics\":{",f);
        for(unsigned j=0;!discovery && j<e->count;j++) {
            struct metric *m=&e->m[j];if(j) fputc(',',f);quoted(f,m->name);fputs(":{\"value\":",f);
            if(m->valid) fprintf(f,"%" PRIu64,m->value);else fputs("null",f);
            fputs(",\"status\":",f);quoted(f,m->valid?"PASS":"MISSING");fputs(",\"reason\":",f);quoted(f,m->reason);fputs(",\"unit\":",f);quoted(f,m->unit);fputs(",\"kind\":",f);quoted(f,m->gauge?"gauge":"counter");
            if(!m->gauge) interval(f,e,m);
            fputc('}',f);
        }fputs("}}",f);
    }fputs("],\"raw\":[",f);
    for(unsigned i=0;i<raw_count;i++) {if(i) fputc(',',f);fputs("{\"path\":",f);quoted(f,raw_path[i]);fputs(",\"text\":",f);quoted(f,raw[i]);fputc('}',f);}fputs("]}\n",f);
}
static void identity(void) {
    boot[0]=ns[0]=context[0]=kernel[0]=0;
    char *s=text("/proc/sys/kernel/random/boot_id");if(s) {trim(s);if(strlen(s)<sizeof boot) strcpy(boot,s);free(s);}
    char path[MAX_PATH];if(!rooted("/proc/self/ns/mnt",path)) {ssize_t n=readlink(path,ns,sizeof ns-1);if(n>=0) ns[n]=0;}
    struct utsname u;if(!uname(&u)) fmt(kernel,sizeof kernel,"%s",u.release);
    fmt(context,sizeof context,"%s|%s|%s|%s|%s",node,kernel,boot,ns,fixture);
}
static int worker(FILE *f) {
    entities=calloc(MAX_ENT,sizeof *entities);if(!entities) return 74;
    status="PASS";reason=NULL;count=mount_count=raw_count=0;start_ns=now(CLOCK_MONOTONIC);identity();load_state();
#if defined(__linux__) || defined(REMORA_IO_TESTING)
    const int supported=1;
#else
    const int supported=0;
#endif
    if(!supported) {status="UNSUPPORTED";reason="LINUX_REQUIRED";}
    else if(!strcmp(provider,"process")) process_sample();
    else if(!parse_mounts()) {
        if(!strcmp(provider,"mounts")) mounts_sample();
        else if(!strcmp(provider,"capacity") || !strcmp(provider,"tmp")) paths_sample(!strcmp(provider,"tmp"));
        else if(!strcmp(provider,"nfs")) nfs_sample();
        else if(!strcmp(provider,"block")) block_sample();
    }
    if(supported && raw_count && !strcmp(raw_path[0],"/proc/self/mountinfo")) {
        char *after=text("/proc/self/mountinfo");
        if(!after || strcmp(after,raw[0])) {
            fail("MOUNT_TABLE_CHANGED");
            for(unsigned i=0;i<count;i++) {entities[i].identity_known=0;entity_fail(&entities[i],"MOUNT_TABLE_CHANGED");for(unsigned j=0;j<entities[i].count;j++) {entities[i].m[j].valid=0;entities[i].m[j].reason="MOUNT_TABLE_CHANGED";}}
        }free(after);
    }
    end_ns=now(CLOCK_MONOTONIC);
    if(!discovery && store_state()) fail("STATE_WRITE_ERROR");
    emit(f);
    return ferror(f)?74:!strcmp(status,"PASS") || !strcmp(status,"SKIP")?0:10;
}
static void on_signal(int sig) {interrupted=sig;}
/* Only the collector's owned provider child receives signals. Partial JSON is discarded. */
static void cleanup_pending(void) {
    if(pending_fd<0) return;
    struct stat a,b;if(!fstat(pending_fd,&a) && !fstatat(state_dir,pending_name,&b,AT_SYMLINK_NOFOLLOW) && a.st_dev==b.st_dev && a.st_ino==b.st_ino) unlinkat(state_dir,pending_name,0);
    close(pending_fd);pending_fd=-1;
}
static int isolated(FILE *out) {
    pending_fd=-1;pending_name[0]=0;
    if(state_dir>=0 && !discovery && (!strcmp(provider,"nfs") || !strcmp(provider,"block") || !strcmp(provider,"process"))) {
        fmt(pending_name,sizeof pending_name,".io_%s_%s.next-%ld",node,provider,(long)getpid());
        pending_fd=owned_file(pending_name,O_WRONLY|O_CREAT|O_EXCL);if(pending_fd<0) return 74;
    }
    int pipefd[2];if(pipe(pipefd)) {cleanup_pending();return 74;}
    fflush(NULL);pid_t pid=fork();if(pid<0) {close(pipefd[0]);close(pipefd[1]);cleanup_pending();return 74;}
    if(!pid) {
        signal(SIGTERM,SIG_DFL);signal(SIGINT,SIG_DFL);signal(SIGHUP,SIG_DFL);
        close(pipefd[0]);
        FILE *f=fdopen(pipefd[1],"w");
        if(!f) _exit(74);
        int r=worker(f);
        if(fclose(f)) r=74;
#ifdef REMORA_IO_TESTING
        if(getenv("IO_TEST_CHILD_TERM_AT_EOF")) raise(SIGTERM);
#endif
        _exit(r);
    }
    close(pipefd[1]);char *buffer=malloc(MAX_OUTPUT+1);size_t used=0;int child_status=0,finished=0,eof=0,bad=0;
    uint64_t deadline=now(CLOCK_MONOTONIC)+(uint64_t)timeout_ms*1000000;
    if(!buffer) bad=1;
    while(!bad && !(finished && eof)) {
        if(interrupted) {bad=3;break;}if(now(CLOCK_MONOTONIC)>=deadline) {bad=2;break;}
        struct pollfd p={pipefd[0],POLLIN,0};int r=poll(&p,1,20);if(r<0 && errno!=EINTR) {bad=1;break;}
        if(r>0 && (p.revents&(POLLIN|POLLHUP))) {
            ssize_t n=read(pipefd[0],buffer+used,MAX_OUTPUT-used);
            if(n>0) {used+=(size_t)n;if(used==MAX_OUTPUT) bad=4;}
            else if(!n) eof=1;else if(errno!=EINTR) bad=1;
        }
        if(!finished) {pid_t rpid=waitpid(pid,&child_status,WNOHANG);if(rpid==pid) finished=1;else if(rpid<0 && errno!=EINTR) {bad=1;break;}}
    }
#ifdef REMORA_IO_TESTING
    /* Reproduce a signal delivered during the last EOF/reap loop iteration. */
    if(finished && eof && getenv("IO_TEST_PARENT_TERM_AT_EOF")) raise(SIGTERM);
#endif
    close(pipefd[0]);if(!finished) {kill(pid,SIGKILL);while(waitpid(pid,&child_status,0)<0 && errno==EINTR) {}finished=1;}
    /* EOF/reap can finish the loop in the same iteration that delivered a
       signal. Honor it before interpreting child status or committing output. */
    if(interrupted) bad=3;
    int code=WIFEXITED(child_status)?WEXITSTATUS(child_status):74;
    if(bad || (code!=0 && code!=10)) {
        invalidate_state();if(bad==3) code=128+interrupted;
        else {entities=NULL;count=raw_count=0;status=bad==2?"TIMEOUT":"INVALID";reason=bad==2?"PROVIDER_DEADLINE":bad==4?"OUTPUT_LIMIT":"PROVIDER_FAILURE";start_ns=end_ns=now(CLOCK_MONOTONIC);boot[0]=ns[0]=0;emit(out);code=10;}
    } else if(used && buffer[used-1]=='\n') {if(fwrite(buffer,1,used,out)!=used) code=74;}
    else {invalidate_state();code=74;}
    if(fflush(out)) code=74;
    if(pending_fd>=0) {
        struct stat a,b;char name[256];state_name(name);
        if(!bad && (code==0 || code==10) && !fstat(pending_fd,&a) && a.st_size>0) {
            int safe=fstatat(state_dir,name,&b,AT_SYMLINK_NOFOLLOW)?errno==ENOENT:S_ISREG(b.st_mode) && b.st_uid==geteuid() && b.st_nlink==1;
            if(!safe || renameat(state_dir,pending_name,state_dir,name)) code=74;
        }
        cleanup_pending();
    }
    if(code==74) invalidate_state();
    free(buffer);return code;
}
static int split_list(const char *s,const char *delimiter,const char **out,unsigned *n) {
    if(!s || !*s || strlen(s)>32768) return -1;
    char *copy=strdup(s),*save=NULL;if(!copy) return -1;
    for(char *p=strtok_r(copy,delimiter,&save);p;p=strtok_r(NULL,delimiter,&save)) {if(*n==64) return -1;out[(*n)++]=p;}return *n?0:-1;
}
static int usage(void) {fputs("usage: data_collect_io [NODE OUTDIR TMPDIR] | [--list|--describe|--discover|--check] [--modules CSV] [--path ABS] [--device MAJOR:MINOR] [--pid PID:START] [--node NAME] [--state-dir DIR] [--timeout-ms N] [--allow-partial]\n",stderr);return 64;}
int main(int argc,char **argv) {
    int selected[6]={1,1,1,1,1,1},list=0,check=0,lock=-1;const char *selection=getenv("REMORA_RESOURCE_MODULES"),*directory=NULL;
    if(gethostname(node,sizeof node)) strcpy(node,"unknown");
    node[sizeof node-1]=0;
    int i=1;
    if(argc==4 && argv[1][0]!='-') {legacy=allow_partial=1;fmt(node,sizeof node,"%s",argv[1]);directory=argv[3];i=4;}
    for(;i<argc;i++) {
        const char *arg=argv[i];
        if(!strcmp(arg,"--list") || !strcmp(arg,"--describe")) {list=1;continue;}
        if(!strcmp(arg,"--discover")) {discovery=1;continue;}
        if(!strcmp(arg,"--check")) {check=1;continue;}
        if(!strcmp(arg,"--allow-partial")) {allow_partial=1;continue;}
        if(++i==argc) return usage();
        if(!strcmp(arg,"--modules")) selection=argv[i];
        else if(!strcmp(arg,"--node")) {if(fmt(node,sizeof node,"%s",argv[i])) return usage();}
        else if(!strcmp(arg,"--state-dir")) directory=argv[i];
        else if(!strcmp(arg,"--path") && path_count<64) paths[path_count++]=argv[i];
        else if(!strcmp(arg,"--device") && device_count<64) devices[device_count++]=argv[i];
        else if(!strcmp(arg,"--pid") && handle_count<64) handles[handle_count++]=argv[i];
        else if(!strcmp(arg,"--timeout-ms")) {uint64_t n;if(uint_value(argv[i],&n) || n<10 || n>60000) return usage();timeout_ms=(unsigned)n;}
#ifdef REMORA_IO_TESTING
        else if(!strcmp(arg,"--fixture-root")) {if(!realpath(argv[i],fixture)) return usage();}
#endif
        else return usage();
    }
    if(!safe_name(node)) return usage();
    if(selection) {
        size_t n=strlen(selection);if(!n || selection[0]==',' || selection[n-1]==',' || strstr(selection,",,")) return usage();
        memset(selected,0,sizeof selected);const char *a[64];unsigned width=0;if(split_list(selection,",",a,&width)) return usage();
        for(unsigned j=0;j<width;j++) {unsigned k=0;for(;k<6;k++) if(!strcmp(providers[k],a[j])) break;if(k==6 || selected[k]) return usage();selected[k]=1;}
    }
    if(!path_count && getenv("REMORA_RESOURCE_PATHS") && split_list(getenv("REMORA_RESOURCE_PATHS"),"\n",paths,&path_count)) return usage();
    if(!device_count && getenv("REMORA_RESOURCE_DEVICES") && split_list(getenv("REMORA_RESOURCE_DEVICES"),",",devices,&device_count)) return usage();
    const char *env_handles=getenv("REMORA_RESOURCE_PROCESS_HANDLES");
    if(!handle_count && env_handles && *env_handles) {
        const char *rows[64];unsigned n=0;if(split_list(env_handles,"\n",rows,&n)) return usage();
        for(unsigned j=0;j<n;j++) {char *t[3];unsigned width=tokens((char*)rows[j],t,3);if(width!=2 || !safe_name(t[0])) return usage();if(!strcmp(t[0],node)) handles[handle_count++]=t[1];}
    }
    const char *limit=getenv("REMORA_RESOURCE_TIMEOUT");
    if(limit) {uint64_t seconds;if(uint_value(limit,&seconds) || !seconds || seconds>60) return usage();timeout_ms=(unsigned)seconds*1000;}
    for(unsigned j=0;j<path_count;j++) if(paths[j][0]!='/' || strlen(paths[j])>=MAX_PATH) return usage();
    for(unsigned j=0;j<path_count;j++) for(unsigned k=0;k<j;k++) if(!strcmp(paths[j],paths[k])) return usage();
    for(unsigned j=0;j<device_count;j++) {if(!device_name(devices[j])) return usage();for(unsigned k=0;k<j;k++) if(!strcmp(devices[j],devices[k])) return usage();}
    for(unsigned j=0;j<handle_count;j++) {
        char h[128];if(fmt(h,sizeof h,"%s",handles[j])) return usage();char *colon=strchr(h,':');uint64_t pid,ticks;
        if(!colon) return usage();
        *colon=0;
        if(uint_value(h,&pid) || !pid || pid>INT_MAX || uint_value(colon+1,&ticks)) return usage();
        for(unsigned k=0;k<j;k++) if(!strcmp(handles[k],handles[j])) return usage();
    }
    if(list) {
        for(unsigned j=0;j<6;j++) if(selected[j]) {
            printf("{\"id\":\"%s\",\"version\":\"1\",\"scope\":\"%s\",\"os\":\"Linux\",\"privilege\":\"unprivileged_read_permission\",\"runtime\":\"C\",\"availability\":\"NOT_PROBED\",\"recommended_cadence_seconds\":10,\"default_timeout_ms\":3000,\"output_limit_bytes\":%d,\"source_limit_bytes\":%d,\"backend\":",providers[j],scopes[j],MAX_OUTPUT,MAX_FILE);
            quoted(stdout,backends[j]);fputs(",\"discovery\":\"--discover\",\"contract\":\"docs/io-resources.md\"}\n",stdout);
        }
        return ferror(stdout)?74:0;
    }if(check) return 0;
    if(directory) {
        state_dir=open(directory,O_RDONLY|O_DIRECTORY|O_NOFOLLOW);struct stat st;
        if(state_dir<0 || fstat(state_dir,&st) || st.st_uid!=geteuid()) {fputs("io: unsafe state directory\n",stderr);return 74;}
        char name[256];fmt(name,sizeof name,".io_%s.lock",node);lock=owned_file(name,O_WRONLY|O_CREAT);
        if(lock<0 || flock(lock,LOCK_EX|LOCK_NB)) {fputs("io: collector lock unavailable\n",stderr);return 74;}
    }
    FILE *output=stdout;
    if(legacy) {
        char name[256];fmt(name,sizeof name,"io_%s.jsonl",node);int fd=owned_file(name,O_WRONLY|O_APPEND);if(fd<0) {fputs("io: run output must be initialized\n",stderr);return 74;}output=fdopen(fd,"a");if(!output) return 74;
    }
    signal(SIGTERM,on_signal);signal(SIGINT,on_signal);signal(SIGHUP,on_signal);signal(SIGPIPE,SIG_IGN);
    int result=0;
    for(unsigned j=0;j<6;j++) if(selected[j]) {
        if(interrupted) {result=128+interrupted;break;}
        provider=providers[j];int r=isolated(output);if(r>=128 || r==74) {result=r;break;}if(r) result=allow_partial?0:2;
    }
    if(output!=stdout && fclose(output)) result=74;
    if(lock>=0) close(lock);
    if(state_dir>=0) close(state_dir);
    return interrupted?128+interrupted:result;
}
