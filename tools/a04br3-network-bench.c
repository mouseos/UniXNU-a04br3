/* SPDX-License-Identifier: MIT
 * Explicit application TCP goodput. No automatic boot execution or DNS. */
#define _POSIX_C_SOURCE 200809L
#ifdef __APPLE__
#define _DARWIN_C_SOURCE 1
#endif
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <ifaddrs.h>
#include <inttypes.h>
#include <net/if.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>
#ifdef __APPLE__
#include <net/if_dl.h>
#else
#include <linux/if_link.h>
#include <netpacket/packet.h>
#endif
#ifndef A04_NETWORK_DEADLINE_NS
#define A04_NETWORK_DEADLINE_NS 30000000000ULL
#endif
static uint64_t now_ns(void) {
 struct timespec t;if(clock_gettime(CLOCK_MONOTONIC,&t)){perror("clock_gettime");exit(1);}
 return (uint64_t)t.tv_sec*1000000000ULL+(uint64_t)t.tv_nsec;
}
static int counters(const char *phase) {
 struct ifaddrs *list=NULL;
 if(getifaddrs(&list)){fprintf(stderr,"interface counters unavailable errno=%d\n",errno);return 0;}
 unsigned seen=0;
 for(struct ifaddrs *p=list;p;p=p->ifa_next) {
  if(!p->ifa_addr || !p->ifa_data)continue;
#ifdef __APPLE__
  if(p->ifa_addr->sa_family!=AF_LINK)continue;
  const struct if_data *d=(const struct if_data *)p->ifa_data;
#else
  if(p->ifa_addr->sa_family!=AF_PACKET)continue;
  const struct rtnl_link_stats *d=(const struct rtnl_link_stats *)p->ifa_data;
#endif
  /* Names are kernel interface identifiers, not arbitrary unescaped JSON. */
  char name[IF_NAMESIZE];size_t at=0;
  for(const char *q=p->ifa_name;*q && at+1<sizeof name;++q)
   if((*q>='a'&&*q<='z')||(*q>='A'&&*q<='Z')||(*q>='0'&&*q<='9')||*q=='_'||*q=='-')name[at++]=*q;
  name[at]=0;
#ifdef __APPLE__
  uint64_t rx=d->ifi_ibytes,tx=d->ifi_obytes,rp=d->ifi_ipackets,tp=d->ifi_opackets;
  unsigned bits=sizeof(d->ifi_ibytes)*8;
#else
  uint64_t rx=d->rx_bytes,tx=d->tx_bytes,rp=d->rx_packets,tp=d->tx_packets;
  unsigned bits=sizeof(d->rx_bytes)*8;
#endif
  ++seen;
  printf("{\"phase\":\"%s\",\"interface\":\"%s\",\"at_ns\":%"PRIu64",\"rx_bytes\":%"PRIu64",\"tx_bytes\":%"PRIu64",\"rx_packets\":%"PRIu64",\"tx_packets\":%"PRIu64",\"counter_bits\":%u,\"non_atomic\":true}\n",phase,name,now_ns(),rx,tx,rp,tp,bits);
 }
 freeifaddrs(list);return seen!=0;
}
static int ready(int fd,short events,uint64_t deadline) {
 for(;;){uint64_t now=now_ns();if(now>=deadline){errno=ETIMEDOUT;return 0;}
  int ms=(int)((deadline-now+999999)/1000000);struct pollfd p={fd,events,0};
  int n=poll(&p,1,ms);if(n<0&&errno==EINTR)continue;
  if(n>0)return 1;
  if(!n)errno=ETIMEDOUT;
  return 0;
 }
}
static int transfer(int fd,void *data,size_t bytes,int sending,uint64_t deadline) {
 unsigned char *p=data;size_t done=0;
 while(done<bytes){if(!ready(fd,sending?POLLOUT:POLLIN,deadline))return 0;
  ssize_t n=sending?send(fd,p+done,bytes-done,0):recv(fd,p+done,bytes-done,0);
  if(n<0&&(errno==EINTR||errno==EAGAIN||errno==EWOULDBLOCK))continue;
  if(n<=0){if(!n)errno=ECONNRESET;return 0;}done+=(size_t)n;
 }return 1;
}
static uint32_t hash(uint32_t h,const unsigned char *p,size_t n){while(n--){h^=*p++;h*=16777619U;}return h;}
static unsigned char pattern(uint32_t at){return (unsigned char)((at*17U+(at>>12)+37U)&255U);}
static uint32_t number(const char *s,uint32_t max){char *end;errno=0;unsigned long v=strtoul(s,&end,10);
 if(errno||!*s||*s=='-'||*end||!v||v>max){fprintf(stderr,"invalid numeric argument\n");exit(2);}return (uint32_t)v;}
static int serve(const char *ip,const char *port) {
 int listener=-1,fd=-1,error=0;struct sockaddr_in a={0};a.sin_family=AF_INET;
 if(inet_pton(AF_INET,ip,&a.sin_addr)!=1)return 2;
 a.sin_port=htons((uint16_t)number(port,65535));listener=socket(AF_INET,SOCK_STREAM,0);
 if(listener<0)return 1;
 int one=1;(void)setsockopt(listener,SOL_SOCKET,SO_REUSEADDR,&one,sizeof one);
 if(bind(listener,(struct sockaddr *)&a,sizeof a)||listen(listener,1)||fcntl(listener,F_SETFL,O_NONBLOCK)<0)goto done;
 printf("{\"phase\":\"listening\"}\n");fflush(stdout);
 uint64_t deadline=now_ns()+A04_NETWORK_DEADLINE_NS;
 if(!ready(listener,POLLIN,deadline))goto done;
 fd=accept(listener,NULL,NULL);if(fd<0||fcntl(fd,F_SETFL,O_NONBLOCK)<0)goto done;
 uint32_t header[4];if(!transfer(fd,header,sizeof header,0,deadline))goto done;
 uint32_t bytes=ntohl(header[2]);
 if(ntohl(header[0])!=0x4130344eU||ntohl(header[1])!=1||ntohl(header[3])!=37||!bytes||bytes>16777216){errno=EPROTO;goto done;}
 unsigned char b[65536];uint32_t at=0,h=2166136261U,ack=0;uint64_t verify_ns=0,generate_ns=0;
 while(at<bytes){size_t n=bytes-at;if(n>sizeof b)n=sizeof b;if(!transfer(fd,b,n,0,deadline))goto done;
  uint64_t began=now_ns();for(size_t i=0;i<n;i++)if(b[i]!=pattern(at+(uint32_t)i)){errno=EBADMSG;goto done;}
  h=hash(h,b,n);verify_ns+=now_ns()-began;at+=(uint32_t)n;}
 ack=htonl(h);if(!transfer(fd,&ack,4,1,deadline))goto done;at=0;h=2166136261U;
 while(at<bytes){size_t n=bytes-at;if(n>sizeof b)n=sizeof b;uint64_t began=now_ns();
  for(size_t i=0;i<n;i++)b[i]=(unsigned char)(pattern(at+(uint32_t)i)^0xa5U);
  h=hash(h,b,n);generate_ns+=now_ns()-began;if(!transfer(fd,b,n,1,deadline))goto done;at+=(uint32_t)n;}
 if(!transfer(fd,&ack,4,0,deadline)||ntohl(ack)!=h){errno=EBADMSG;goto done;}
 ack=htonl(0x50415353U);if(!transfer(fd,&ack,4,1,deadline))goto done;
 printf("{\"phase\":\"server_complete\",\"verified\":true,\"server_upload_verify_ns\":%"PRIu64",\"server_download_generate_hash_ns\":%"PRIu64"}\n",verify_ns,generate_ns);
 close(fd);close(listener);return 0;
 done:error=errno;if(fd>=0)close(fd);if(listener>=0)close(listener);fprintf(stderr,"server failed errno=%d\n",error);return 1;
}
int main(int argc,char **argv){
 signal(SIGPIPE,SIG_IGN);
 if(argc==4&&!strcmp(argv[1],"--serve"))return serve(argv[2],argv[3]);
 if(argc==2&&!strcmp(argv[1],"--snapshot")){return counters("snapshot")?0:1;}
 if(argc!=5||strcmp(argv[1],"--run")){fprintf(stderr,"usage: %s --snapshot | --run numeric_ipv4 port bytes(1..16777216)\n",argv[0]);return 2;}
 struct sockaddr_in a={0};a.sin_family=AF_INET;
 if(inet_pton(AF_INET,argv[2],&a.sin_addr)!=1){fprintf(stderr,"numeric IPv4 required\n");return 2;}
 a.sin_port=htons((uint16_t)number(argv[3],65535));uint32_t bytes=number(argv[4],16777216);
 signal(SIGPIPE,SIG_IGN);int before_ok=counters("before");uint64_t deadline=now_ns()+A04_NETWORK_DEADLINE_NS;
 int fd=socket(AF_INET,SOCK_STREAM,0);if(fd<0){perror("socket");return 1;}
 if(fcntl(fd,F_SETFL,O_NONBLOCK)<0)goto fail;
 if(connect(fd,(struct sockaddr *)&a,sizeof a)<0&&errno!=EINPROGRESS)goto fail;
 if(!ready(fd,POLLOUT,deadline))goto fail;
 int error=0;socklen_t len=sizeof error;if(getsockopt(fd,SOL_SOCKET,SO_ERROR,&error,&len)||error){if(error)errno=error;goto fail;}
 uint32_t header[4]={htonl(0x4130344e),htonl(1),htonl(bytes),htonl(37)};
 if(!transfer(fd,header,sizeof header,1,deadline))goto fail;
 unsigned char buffer[65536];uint32_t checksum=2166136261U,at=0,ack=0;
 uint64_t upload_prepare_ns=0,download_check_ns=0;
 uint64_t start=now_ns();
 while(at<bytes){size_t n=bytes-at;if(n>sizeof buffer)n=sizeof buffer;
  uint64_t prepare=now_ns();
  for(size_t i=0;i<n;i++)buffer[i]=pattern(at+(uint32_t)i);
  checksum=hash(checksum,buffer,n);upload_prepare_ns+=now_ns()-prepare;if(!transfer(fd,buffer,n,1,deadline))goto fail;at+=(uint32_t)n;}
 if(!transfer(fd,&ack,sizeof ack,0,deadline)||ntohl(ack)!=checksum){errno=EBADMSG;goto fail;}
 uint64_t upload=now_ns()-start;start=now_ns();checksum=2166136261U;at=0;
 while(at<bytes){size_t n=bytes-at;if(n>sizeof buffer)n=sizeof buffer;
  if(!transfer(fd,buffer,n,0,deadline))goto fail;
  uint64_t check=now_ns();
  for(size_t i=0;i<n;i++)if(buffer[i]!=(unsigned char)(pattern(at+(uint32_t)i)^0xa5U)){errno=EBADMSG;goto fail;}
  checksum=hash(checksum,buffer,n);download_check_ns+=now_ns()-check;at+=(uint32_t)n;}
 uint64_t download=now_ns()-start;ack=htonl(checksum);
 if(!transfer(fd,&ack,sizeof ack,1,deadline))goto fail;
 /* Server confirms it received and validated our final acknowledgement. */
 if(!transfer(fd,&ack,sizeof ack,0,deadline)||ntohl(ack)!=0x50415353U){errno=EBADMSG;goto fail;}
 close(fd);int after_ok=counters("after");
 printf("{\"phase\":\"client_processing\",\"upload_generate_hash_ns\":%"PRIu64",\"download_validate_hash_ns\":%"PRIu64"}\n",upload_prepare_ns,download_check_ns);
 printf("{\"phase\":\"complete\",\"verified\":true,\"interface_counters_available\":%s,\"bytes_each_direction\":%u,\"upload_delivery_ns\":%"PRIu64",\"download_validate_ns\":%"PRIu64",\"upload_MiB_s\":%.6f,\"download_MiB_s\":%.6f,\"scope\":\"TCP application goodput including both endpoint CPU generation/hash/validation and RTT; not radio/link rate\"}\n",before_ok&&after_ok?"true":"false",bytes,upload,download,upload?bytes*1e9/upload/1048576:0,download?bytes*1e9/download/1048576:0);
 return 0;
fail:
 {int saved=errno;close(fd);fprintf(stderr,"network measurement failed errno=%d (%s)\n",saved,strerror(saved));return 1;}
}
