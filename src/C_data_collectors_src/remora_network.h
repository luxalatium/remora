/* MIT. Shared eth/ib/opa reader. See docs/network-modules.md. */
#ifndef REMORA_NETWORK_H
#define REMORA_NETWORK_H
#define _POSIX_C_SOURCE 200809L
#define _DEFAULT_SOURCE 1
#define _DARWIN_C_SOURCE 1
#include <sys/types.h>
#include <sys/stat.h>
#include <sys/file.h>
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

#define RN_MAX 64
#define RN_PATH 768
#define RN_METRICS 8
#define RN_MAGIC UINT64_C(0x524e455430303031)
struct rn_metric { char path[RN_PATH]; uint64_t value, scale; };
struct rn_entity { char name[128], identity[192]; struct rn_metric m[RN_METRICS]; };
struct rn_state {
    uint64_t magic, check; unsigned count, width, valid;
    int64_t sec, nsec, mono_sec, mono_nsec;
    char root[RN_PATH], node[128];
    struct rn_entity e[RN_MAX];
};
static const char *rn_names[] = {"tx_packets", "rx_packets", "tx_bytes", "rx_bytes",
                                "tx_err", "tx_dropped", "rx_frame_err", "rx_crc_err"};
static const char *rn_eth[] = {"tx_packets", "rx_packets", "tx_bytes", "rx_bytes",
                              "tx_errors", "tx_dropped", "rx_frame_errors", "rx_crc_errors"};
static const char *rn_ib[] = {"port_xmit_packets", "port_rcv_packets", "port_xmit_data", "port_rcv_data"};
static const char *rn_hw[] = {"TxPkt", "RxPkt", "TxWords", "RxWords"};
static FILE *rn_raw, *rn_events;
static struct rn_state *rn_now;
static char rn_id[64];
static int rn_format(char *s, size_t n, const char *fmt, ...) {
    va_list ap; va_start(ap,fmt); int r=vsnprintf(s,n,fmt,ap); va_end(ap);
    if (r<0 || (size_t)r>=n) { errno=ENAMETOOLONG; return -1; } return 0;
}
static int rn_name(const char *s) {
    if (!s || !*s || strlen(s)>=120 || !strcmp(s,".") || !strcmp(s,"..")) return 0;
    for (;*s;s++) if (!isalnum((unsigned char)*s) && !strchr("_.:-",*s)) return 0;
    return 1;
}
/* Nonblocking, bounded reads: a malicious fixture FIFO must not hang a worker. */
static int rn_text(const char *rel, char *out, size_t size) {
    char p[RN_PATH*2]; struct stat st;
    if (rn_format(p,sizeof p,"%s/%s",rn_now->root,rel)) return -1;
    int fd=open(p,O_RDONLY|O_NONBLOCK); if(fd<0) return -1;
    if (fstat(fd,&st) || !S_ISREG(st.st_mode)) {close(fd);errno=EBADMSG;return -1;}
    ssize_t n=read(fd,out,size-1); int e=errno;
    if (n>=0 && (size_t)n==size-1) {n=-1;e=EOVERFLOW;}
    if (close(fd) && n>=0) {n=-1;e=errno;}
    if (n<0) {errno=e;return -1;} out[n]='\0';
    while(n && isspace((unsigned char)out[n-1])) out[--n]='\0';
    return 0;
}
static int rn_uint(const char *s,uint64_t *v) {
    if(!s || !isdigit((unsigned char)*s)) {errno=EBADMSG;return -1;}
    uint64_t x=0; for(;*s;s++) {
        if(!isdigit((unsigned char)*s) || x>(UINT64_MAX-(unsigned)(*s-'0'))/10) {errno=ERANGE;return -1;}
        x=x*10+(unsigned)(*s-'0');
    } *v=x;return 0;
}
static int rn_fallback(int e) {
    return e==ENOENT || e==ENODEV || e==EINVAL || e==ENOSYS || e==EOPNOTSUPP;
}
static int rn_counter(struct rn_entity *entity, unsigned m, const char *path, uint64_t scale) {
    char text[96]; uint64_t value=0; int r=rn_text(path,text,sizeof text);
    if(!r) r=rn_uint(text,&value);
    int e=r?errno:0;
    if (fprintf(rn_raw,"%s\t%s\t%s\t%s\t",rn_id,entity->name,rn_names[m],path)<0) return -1;
    if (r) fprintf(rn_raw,"NA\t%d\t%" PRIu64 "\t%s\n",e,scale,entity->identity);
    else fprintf(rn_raw,"%" PRIu64 "\t0\t%" PRIu64 "\t%s\n",value,scale,entity->identity);
    if(ferror(rn_raw)) {errno=EIO;return -1;}
    if(r) {errno=e;return -1;}
    struct rn_metric *v=&entity->m[m];
    if(rn_format(v->path,sizeof v->path,"%s",path)) return -1;
    v->value=value;v->scale=scale;return 0;
}
static int rn_metric_read(struct rn_entity *e,unsigned m,int eth,int hfi) {
    char path[RN_PATH];
    if(eth) {
        if(rn_format(path,sizeof path,"class/net/%s/statistics/%s",e->name,rn_eth[m])) return -1;
        return rn_counter(e,m,path,1);
    }
    char dev[128],port[128]; const char *slash=strchr(e->name,'/');
    if(!slash) {errno=EINVAL;return -1;}
    size_t n=(size_t)(slash-e->name);memcpy(dev,e->name,n);dev[n]='\0';
    if(rn_format(port,sizeof port,"%s",slash+1)) return -1;
    for(int source=0;source<3;source++) {
        if(source==2 && !hfi) break;
        const char *dir=source==0?"counters_ext":source==1?"counters":"hw_counters";
        const char *key=source==2?rn_hw[m]:rn_ib[m];
        if(rn_format(path,sizeof path,"class/infiniband/%s/ports/%s/%s/%s%s",dev,port,dir,key,source==0?"_64":"")) return -1;
        uint64_t scale=m<2?1:source==2?4:hfi?8:4;
        if(!rn_counter(e,m,path,scale)) return 0;
        if(!rn_fallback(errno)) return -1;
    } return -1;
}
static int rn_active(const char *name,int eth,int explicit_selection,int opa,int *hfi) {
    char path[RN_PATH],text[160]; uint64_t x;
    if(eth) {
        *hfi=0;
        if(rn_format(path,sizeof path,"class/net/%s/flags",name) || rn_text(path,text,sizeof text)) return -1;
        char *end; errno=0; unsigned long flags=strtoul(text,&end,0);
        if(errno || end==text || *end) {errno=EBADMSG;return -1;}
        if(!(flags&1)) return 0;
        if(explicit_selection) return 1;
        if(rn_format(path,sizeof path,"class/net/%s/type",name) || rn_text(path,text,sizeof text) || rn_uint(text,&x)) return -1;
        return x==1;
    }
    char dev[128]; const char *slash=strchr(name,'/');
    if(!slash || strchr(slash+1,'/') || slash==name) {errno=EINVAL;return -1;}
    size_t n=(size_t)(slash-name); if(n>=sizeof dev) {errno=EINVAL;return -1;}
    memcpy(dev,name,n);dev[n]='\0';
    if(!rn_name(dev) || !rn_name(slash+1)) {errno=EINVAL;return -1;}
    for(const char *s=slash+1;*s;s++) if(!isdigit((unsigned char)*s)) {errno=EINVAL;return -1;}
    *hfi=!strncmp(dev,"hfi1_",5);
    /* Device names may be renamed: also identify the bound PCI driver. */
    char abs[RN_PATH*2],driver[RN_PATH];
    if(rn_format(abs,sizeof abs,"%s/class/infiniband/%s/device/driver",rn_now->root,dev)) return -1;
    ssize_t len=readlink(abs,driver,sizeof driver-1);
    if(len>=0) {driver[len]='\0';const char *base=strrchr(driver,'/'); if(!strcmp(base?base+1:driver,"hfi1")) *hfi=1;}
    if(opa && !*hfi) return 0;
    if(rn_format(path,sizeof path,"class/infiniband/%s/ports/%s/state",dev,slash+1) || rn_text(path,text,sizeof text)) return -1;
    if(strcmp(text,"4: ACTIVE")) return 0;
    if(rn_format(path,sizeof path,"class/infiniband/%s/ports/%s/link_layer",dev,slash+1) || rn_text(path,text,sizeof text)) return -1;
    return !strcmp(text,"InfiniBand");
}
static int rn_add(const char *name,int eth,int explicit_selection,int opa) {
    int hfi=0,active=rn_active(name,eth,explicit_selection,opa,&hfi);
    if(active<0) {int e=errno;fprintf(stderr,"network: cannot inspect %s: %s\n",name,strerror(e));return e==EINVAL?65:74;}
    if(!active) return explicit_selection?69:0;
    if(rn_now->count>=RN_MAX) {fprintf(stderr,"network: entity limit exceeded\n");return 65;}
    for(unsigned i=0;i<rn_now->count;i++) if(!strcmp(rn_now->e[i].name,name)) return 65;
    struct rn_entity *e=&rn_now->e[rn_now->count++];
    if(rn_format(e->name,sizeof e->name,"%s",name)) return 65;
    char identity_path[RN_PATH];
    if(eth) {
        if(rn_format(identity_path,sizeof identity_path,"class/net/%s/ifindex",name)) return 65;
    } else {
        const char *slash=strchr(name,'/');
        if(rn_format(identity_path,sizeof identity_path,"class/infiniband/%.*s/node_guid",(int)(slash-name),name)) return 65;
    }
    if(rn_text(identity_path,e->identity,sizeof e->identity) || !*e->identity || strpbrk(e->identity,"\t\n\r ")) {
        fprintf(stderr,"network: invalid/missing identity for %s\n",name);return 74;
    }
    int status=0;
    for(unsigned m=0;m<rn_now->width;m++) if(rn_metric_read(e,m,eth,hfi)) {
        fprintf(stderr,"network: %s/%s: %s\n",name,rn_names[m],strerror(errno));status=74;
    }
    return status;
}
static int rn_cmp(const void *a,const void *b) {return strcmp(((const struct rn_entity*)a)->name,((const struct rn_entity*)b)->name);}
static int rn_select(const char *module) {
    int eth=!strcmp(module,"eth"),opa=!strcmp(module,"opa");
    const char *var=eth?"REMORA_ETH_INTERFACES":opa?"REMORA_OPA_PORTS":"REMORA_IB_PORTS";
    const char *selection=getenv(var); int status=0;
    if(selection) {
        size_t len=strlen(selection); if(!len || len>8192 || selection[0]==',' || selection[len-1]==',' || strstr(selection,",,")) return 65;
        char *s=strdup(selection); if(!s) return 74;
        char *save=NULL;
        for(char *n=strtok_r(s,",",&save);n;n=strtok_r(NULL,",",&save)) {
            if((eth && !rn_name(n)) || strlen(n)>=120) {status=65;break;}
            int r=rn_add(n,eth,1,opa); if(r) {status=r;break;}
        } free(s);
    } else {
        char abs[RN_PATH*2]; if(rn_format(abs,sizeof abs,"%s/class/%s",rn_now->root,eth?"net":"infiniband")) return 65;
        DIR *d=opendir(abs); if(!d) return errno==ENOENT?69:74;
        struct dirent *de; unsigned visited=0;
        while((de=readdir(d))) {
            if(de->d_name[0]=='.') continue;
            if(++visited>128) {status=65;break;}
            if(!rn_name(de->d_name)) {status=65;break;}
            if(eth) {int r=rn_add(de->d_name,1,0,0);if(r) status=r;}
            else {
                char ports[RN_PATH*2];if(rn_format(ports,sizeof ports,"%s/%s/ports",abs,de->d_name)) {status=65;break;}
                DIR *pd=opendir(ports);if(!pd) {status=74;break;}
                struct dirent *pe;
                while((pe=readdir(pd))) {
                    if(pe->d_name[0]=='.') continue;
                    if(++visited>256) {status=65;break;}
                    char name[128];if(rn_format(name,sizeof name,"%s/%s",de->d_name,pe->d_name)) {status=65;break;}
                    int r=rn_add(name,0,0,opa);if(r) status=r;
                } closedir(pd);
            }
            if(status) break;
        } closedir(d);
    }
    qsort(rn_now->e,rn_now->count,sizeof rn_now->e[0],rn_cmp);
    return status?status:rn_now->count?0:69;
}
/* All writes are relative to an already opened real run directory. */
static int rn_open(int dir,const char *name,int flags) {
    int fd=openat(dir,name,flags|O_NOFOLLOW|O_NONBLOCK,0600);struct stat st;
    if(fd<0) return -1;
    if(fstat(fd,&st) || !S_ISREG(st.st_mode) || st.st_nlink!=1 || st.st_uid!=geteuid()) {close(fd);errno=EPERM;return -1;}
    return fd;
}
static FILE *rn_log(int dir,const char *name,const char *header) {
    int fd=rn_open(dir,name,O_WRONLY|O_APPEND|O_CREAT);if(fd<0) return NULL;
    struct stat st;if(fstat(fd,&st)) {close(fd);return NULL;}
    FILE *f=fdopen(fd,"a");if(!f) {close(fd);return NULL;}
    if(!st.st_size && fprintf(f,"%s\n",header)<0) {fclose(f);return NULL;} return f;
}
static uint64_t rn_checksum(struct rn_state *s) {
    uint64_t old=s->check,h=UINT64_C(14695981039346656037);s->check=0;
    const unsigned char *p=(const unsigned char*)s;for(size_t i=0;i<sizeof *s;i++) {h^=p[i];h*=UINT64_C(1099511628211);} s->check=old;return h;
}
static int rn_load(int dir,const char *name,struct rn_state *s) {
    int fd=rn_open(dir,name,O_RDONLY);if(fd<0) return errno==ENOENT?0:-1;
    struct stat st;int r=fstat(fd,&st);size_t done=0;
    if(!r && st.st_size==(off_t)sizeof *s) {
        while(done<sizeof *s) {ssize_t n=read(fd,(char*)s+done,sizeof *s-done);if(n<=0) break;done+=(size_t)n;}
    } close(fd);
    if(done!=sizeof *s || s->magic!=RN_MAGIC || s->check!=rn_checksum(s) || s->count>RN_MAX || s->width>RN_METRICS || s->valid>1) {errno=EBADMSG;return -1;}
    return 1;
}
static int rn_store(int dir,const char *name,struct rn_state *s) {
    char tmp[256];if(rn_format(tmp,sizeof tmp,"%s.next",name)) return -1;
    int fd=rn_open(dir,tmp,O_WRONLY|O_CREAT|O_EXCL);if(fd<0) return -1;
    s->check=rn_checksum(s);size_t done=0;
    while(done<sizeof *s) {ssize_t n=write(fd,(char*)s+done,sizeof *s-done);if(n<=0) break;done+=(size_t)n;}
    int r=done==sizeof *s?fsync(fd):-1;int saved=errno;if(close(fd) && !r) {r=-1;saved=errno;}
    if(!r && renameat(dir,tmp,dir,name)) {r=-1;saved=errno;}
    if(r) {errno=saved?saved:EIO;return -1;} return 0;
}
static int rn_same(struct rn_state *a,struct rn_state *b) {
    if(!a->valid || a->count!=b->count || a->width!=b->width || strcmp(a->root,b->root) || strcmp(a->node,b->node)) return 0;
    for(unsigned i=0;i<a->count;i++) {
        if(strcmp(a->e[i].name,b->e[i].name) || strcmp(a->e[i].identity,b->e[i].identity)) return 0;
        for(unsigned j=0;j<a->width;j++) if(strcmp(a->e[i].m[j].path,b->e[i].m[j].path) || a->e[i].m[j].scale!=b->e[i].m[j].scale) return 0;
    } return 1;
}
static int remora_network_main(const char *module,int argc,char **argv) {
    if((argc!=4 && argc!=5) || (argc==5 && strcmp(argv[4],"--prime")) || !rn_name(argv[1])) {fprintf(stderr,"Usage: %s NODE OUTDIR TMPDIR [--prime]\n",argv[0]);return 65;}
    int dir=open(argv[3],O_RDONLY|O_DIRECTORY|O_NOFOLLOW);if(dir<0) {perror("network run directory");return 74;}
    char base[256],lock[280],state[280],pending[280],raw[280],events[280],legacy[280];
    if(rn_format(base,sizeof base,"network-%s-%s",module,argv[1])) {close(dir);return 65;}
    rn_format(lock,sizeof lock,"%s.lock",base);rn_format(state,sizeof state,"%s.state",base);rn_format(pending,sizeof pending,"%s.pending",base);
    rn_format(raw,sizeof raw,"%s.raw.tsv",base);rn_format(events,sizeof events,"%s.events.tsv",base);
    rn_format(legacy,sizeof legacy,!strcmp(module,"eth")?"network_eth_traffic-%s.txt":!strcmp(module,"ib")?"ib_packets-%s.txt":"opa_packets-%s.txt",argv[1]);
    int lfd=rn_open(dir,lock,O_RDWR|O_CREAT);if(lfd<0) {perror("network lock");close(dir);return 74;}
    if(flock(lfd,LOCK_EX|LOCK_NB)) {close(lfd);close(dir);return 75;}
    struct rn_state *old=calloc(1,sizeof *old);rn_now=calloc(1,sizeof *rn_now);
    int status=74,pfd=-1;FILE *output=NULL;
    if(!old || !rn_now) goto done;
    int loaded=rn_load(dir,state,old);if(loaded<0) {perror("network invalid state");status=65;goto done;}
    struct stat st;if(!fstatat(dir,pending,&st,AT_SYMLINK_NOFOLLOW) || errno!=ENOENT) {fprintf(stderr,"network: incomplete transaction; preserve this run and use a new TMPDIR\n");goto done;}
    if(argc==5 && loaded) {status=0;goto done;}
    if(!loaded && !fstatat(dir,legacy,&st,AT_SYMLINK_NOFOLLOW) && st.st_size) {fprintf(stderr,"network: refusing existing unversioned data without state; use a new TMPDIR\n");status=65;goto done;}
    rn_now->magic=RN_MAGIC;rn_now->width=!strcmp(module,"eth")?8:4;
    const char *root=getenv("REMORA_NETWORK_SYSFS_ROOT");if(!root) root="/sys";
    if(root[0]!='/' || strpbrk(root,"\t\r\n") || rn_format(rn_now->root,sizeof rn_now->root,"%s",root) || rn_format(rn_now->node,sizeof rn_now->node,"%s",argv[1])) {status=65;goto done;}
    struct timespec rt,mt;if(clock_gettime(CLOCK_REALTIME,&rt) || clock_gettime(CLOCK_MONOTONIC,&mt)) goto done;
    rn_now->sec=rt.tv_sec;rn_now->nsec=rt.tv_nsec;rn_now->mono_sec=mt.tv_sec;rn_now->mono_nsec=mt.tv_nsec;
    rn_format(rn_id,sizeof rn_id,"%" PRId64 ".%09" PRId64,rn_now->sec,rn_now->nsec);
    pfd=rn_open(dir,pending,O_WRONLY|O_CREAT|O_EXCL);if(pfd<0) goto done;
    rn_raw=rn_log(dir,raw,"# remora-network/v1 sample\tentity\tmetric\tpath\tvalue\terrno\tbyte_scale\tidentity");
    rn_events=rn_log(dir,events,"# remora-network/v1 sample\tstatus\treason\tentities\telapsed_seconds");
    if(!rn_raw || !rn_events) goto done;
    status=rn_select(module);rn_now->valid=status==0;
    const char *event="BASELINE",*reason="first_read";uint64_t sums[RN_METRICS]={0};
    double elapsed=(double)(mt.tv_sec-old->mono_sec)+(mt.tv_nsec-old->mono_nsec)/1e9;
    if(status) {event=status==69?"UNSUPPORTED":"ERROR";reason="selection_or_read_failed";}
    else if(loaded && rn_same(old,rn_now)) {
        event="OK";reason="complete";
        if(elapsed<=0 || rt.tv_sec<old->sec || (rt.tv_sec==old->sec && rt.tv_nsec<=old->nsec)) {event="GAP";reason="clock_change";}
        for(unsigned i=0;i<rn_now->count;i++) for(unsigned j=0;j<rn_now->width;j++) {
            struct rn_metric *v=&rn_now->e[i].m[j],*p=&old->e[i].m[j];
            if(v->value<p->value || v->value==UINT64_MAX || p->value==UINT64_MAX) {event="GAP";reason="reset_or_saturated";continue;}
            uint64_t diff=v->value-p->value;
            if(diff>UINT64_MAX/v->scale || sums[j]>UINT64_MAX-diff*v->scale) {event="GAP";reason="overflow";continue;}
            sums[j]+=diff*v->scale;
        }
    } else if(loaded) {event="GAP";reason="selection_source_or_recovery";}
    fprintf(rn_events,"%s\t%s\t%s\t%u\t%.9f\n",rn_id,event,reason,rn_now->count,loaded?elapsed:0.0);
    /* Flush evidence first. The marker remains on any partial output failure. */
    if(fflush(rn_raw) || fflush(rn_events) || ferror(rn_raw) || ferror(rn_events) || fsync(fileno(rn_raw)) || fsync(fileno(rn_events))) {status=74;goto done;}
    if(!strcmp(event,"OK") || (!loaded && !status)) {
        output=rn_log(dir,legacy,!strcmp(module,"eth")?"time tx_packets rx_packets tx_bytes rx_bytes tx_err tx_dropped rx_frame_err rx_crc_err":"TIMESTAMP(sec) tx_packets rx_packets tx_bytes rx_bytes");
        if(!output) {status=74;goto done;}
        if(!strcmp(event,"OK")) {
            double now=(double)rt.tv_sec+rt.tv_nsec/1e9, before=(double)old->sec+old->nsec/1e9;
            fprintf(output,"%.6f",before+(now-before)/2);
            for(unsigned j=0;j<rn_now->width;j++) fprintf(output," %" PRIu64,sums[j]);
            fprintf(output,"\n");
        }
        if(fflush(output) || ferror(output) || fsync(fileno(output))) {status=74;goto done;}
    }
    if(rn_store(dir,state,rn_now) || unlinkat(dir,pending,0)) {status=74;goto done;}
 done:
    if(output && fclose(output)) status=74;
    if(rn_raw && fclose(rn_raw)) status=74;
    if(rn_events && fclose(rn_events)) status=74;
    rn_raw=rn_events=NULL;
    if(pfd>=0) close(pfd);
    free(old);free(rn_now);rn_now=NULL;close(lfd);close(dir);return status;
}
#endif
