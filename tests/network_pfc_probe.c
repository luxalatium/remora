/* Development-only Linux RTM_GETDCB probe; no setters, no runtime installation. */
#define _POSIX_C_SOURCE 200809L
#include <errno.h>
#include <inttypes.h>
#include <linux/dcbnl.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <poll.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

static void hex(const unsigned char *p, size_t n) {
    size_t i; for(i=0;i<n;i++) printf("%02x",p[i]);
}
static int finish(const char *iface,const char *stage,int error,
                  const unsigned char *raw,size_t bytes,
                  const struct ieee_pfc *pfc,const struct ieee_ets *ets) {
    unsigned i;
    printf("{\"interface\":\"%s\",\"api\":\"RTM_GETDCB/DCB_CMD_IEEE_GET\",\"stage\":\"%s\",\"errno\":%d,\"status\":\"%s\",\"response_hex\":\"",iface,stage,error,error?"SKIP":"OK");
    hex(raw,bytes);printf("\",\"pfc\":");
    if(!pfc) printf("null");
    else {
        printf("{\"cap\":%u,\"enabled_mask\":%u,\"priority_enabled\":[",pfc->pfc_cap,pfc->pfc_en);
        for(i=0;i<8;i++) printf("%s%s",i?",":"",pfc->pfc_en&(1U<<i)?"true":"false");
        printf("],\"unit\":\"frames\",\"scope\":\"driver_DCB_index_requires_mapping\",\"requests\":[");
        for(i=0;i<8;i++) printf("%s\"%" PRIu64 "\"",i?",":"",(uint64_t)pfc->requests[i]);
        printf("],\"indications\":[");
        for(i=0;i<8;i++) printf("%s\"%" PRIu64 "\"",i?",":"",(uint64_t)pfc->indications[i]);
        printf("]}");
    }
    printf(",\"priority_to_tc\":");
    if(!ets) printf("null");
    else {printf("[");for(i=0;i<8;i++)printf("%s%u",i?",":"",ets->prio_tc[i]);printf("]");}
    printf("}\n");
    return error?69:0;
}
int main(int argc,char **argv) {
    union {struct nlmsghdr alignment; unsigned char data[256];} req;
    union {struct nlmsghdr alignment; unsigned char data[65536];} reply;
    unsigned char *request=req.data,*response=reply.data;
    struct nlmsghdr *nh=(struct nlmsghdr *)(void *)request;
    struct dcbmsg *dcb;struct nlattr *attr;struct sockaddr_nl peer={0};
    struct ieee_pfc pfc;struct ieee_ets ets;int has_pfc=0,has_ets=0,fd,error=0;
    size_t name_length,offset;ssize_t bytes;struct pollfd poller;socklen_t peer_length=sizeof(peer);
    const char *iface;
    if(argc!=2)return 65;
    memset(request,0,sizeof(req.data));memset(response,0,sizeof(reply.data));
    iface=argv[1];name_length=strlen(iface);
    if(!name_length || name_length>=IF_NAMESIZE || strspn(iface,"ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.:@-")!=name_length)return 65;
    nh->nlmsg_len=NLMSG_LENGTH(sizeof(*dcb));nh->nlmsg_type=RTM_GETDCB;
    nh->nlmsg_flags=NLM_F_REQUEST;nh->nlmsg_seq=(uint32_t)getpid();
    dcb=NLMSG_DATA(nh);dcb->dcb_family=AF_UNSPEC;dcb->cmd=DCB_CMD_IEEE_GET;
    attr=(struct nlattr *)(void *)(request+NLMSG_ALIGN(nh->nlmsg_len));
    attr->nla_type=DCB_ATTR_IFNAME;attr->nla_len=(uint16_t)(NLA_HDRLEN+name_length+1);
    memcpy((unsigned char *)attr+NLA_HDRLEN,iface,name_length+1);
    nh->nlmsg_len=(uint32_t)(NLMSG_ALIGN(nh->nlmsg_len)+NLA_ALIGN(attr->nla_len));
    fd=socket(AF_NETLINK,SOCK_RAW,NETLINK_ROUTE);
    if(fd<0)return finish(iface,"socket",errno,NULL,0,NULL,NULL);
    peer.nl_family=AF_NETLINK;
    if(sendto(fd,request,nh->nlmsg_len,0,(struct sockaddr *)&peer,sizeof(peer))!=(ssize_t)nh->nlmsg_len) {
        error=errno?errno:EIO;close(fd);return finish(iface,"send",error,NULL,0,NULL,NULL);
    }
    poller.fd=fd;poller.events=POLLIN;poller.revents=0;
    error=poll(&poller,1,5000);
    if(error!=1) {
        error=error==0?ETIMEDOUT:errno;close(fd);return finish(iface,"poll",error,NULL,0,NULL,NULL);
    }
    bytes=recvfrom(fd,response,sizeof(reply.data),MSG_TRUNC,(struct sockaddr *)&peer,&peer_length);
    error=errno;close(fd);
    if(bytes<0)return finish(iface,"receive",error,NULL,0,NULL,NULL);
    if((size_t)bytes>sizeof(reply.data) || peer.nl_pid!=0)return finish(iface,"receive_bounds",EPROTO,NULL,0,NULL,NULL);
    if((size_t)bytes<NLMSG_LENGTH(sizeof(*dcb)))return finish(iface,"header",EPROTO,response,(size_t)bytes,NULL,NULL);
    nh=(struct nlmsghdr *)(void *)response;
    if(nh->nlmsg_len>(size_t)bytes || nh->nlmsg_seq!=(uint32_t)getpid())return finish(iface,"identity",EPROTO,response,(size_t)bytes,NULL,NULL);
    if(nh->nlmsg_type==NLMSG_ERROR) {
        struct nlmsgerr msg;
        if(nh->nlmsg_len<NLMSG_LENGTH(sizeof(msg)))return finish(iface,"error_bounds",EPROTO,response,(size_t)bytes,NULL,NULL);
        memcpy(&msg,NLMSG_DATA(nh),sizeof(msg));
        return finish(iface,"kernel",msg.error<0?-msg.error:EPROTO,response,(size_t)bytes,NULL,NULL);
    }
    if(nh->nlmsg_type!=RTM_GETDCB)return finish(iface,"reply_type",EPROTO,response,(size_t)bytes,NULL,NULL);
    offset=NLMSG_LENGTH(sizeof(*dcb));
    while(offset+NLA_HDRLEN<=nh->nlmsg_len) {
        size_t nested,end;unsigned type;
        attr=(struct nlattr *)(void *)(response+offset);
        if(attr->nla_len<NLA_HDRLEN || offset+attr->nla_len>nh->nlmsg_len)return finish(iface,"attribute_bounds",EPROTO,response,(size_t)bytes,NULL,NULL);
        type=attr->nla_type&0x3fffU;
        if(type==DCB_ATTR_IEEE) {
            nested=offset+NLA_HDRLEN;end=offset+attr->nla_len;
            while(nested+NLA_HDRLEN<=end) {
                struct nlattr *a=(struct nlattr *)(void *)(response+nested);unsigned t=a->nla_type&0x3fffU;
                if(a->nla_len<NLA_HDRLEN || nested+a->nla_len>end)return finish(iface,"nested_bounds",EPROTO,response,(size_t)bytes,NULL,NULL);
                if(t==DCB_ATTR_IEEE_PFC && (size_t)(a->nla_len-NLA_HDRLEN)>=sizeof(pfc)){memcpy(&pfc,(unsigned char *)a+NLA_HDRLEN,sizeof(pfc));has_pfc=1;}
                if(t==DCB_ATTR_IEEE_ETS && (size_t)(a->nla_len-NLA_HDRLEN)>=sizeof(ets)){memcpy(&ets,(unsigned char *)a+NLA_HDRLEN,sizeof(ets));has_ets=1;}
                nested+=NLA_ALIGN(a->nla_len);
            }
        }
        offset+=NLA_ALIGN(attr->nla_len);
    }
    return finish(iface,"reply",has_pfc?0:EOPNOTSUPP,response,(size_t)bytes,has_pfc?&pfc:NULL,has_ets?&ets:NULL);
}
