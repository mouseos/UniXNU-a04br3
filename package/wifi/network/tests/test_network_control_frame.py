# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Production BSD preauth dispatch + production raw RX adapter + NDRV demux.
IOKit/DLIL are typed host mocks: no socket listener or handshake success claim.
"""
from pathlib import Path
import importlib.util
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('bsd_control_fixture',ROOT/'tests/test_bsd_network.py')
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
EXTRA=r'''
#include "A04BR3NetworkControlFrame.h"
static int rxReceived;
static A04BR3BSDNetwork *rxBridge;
struct A04BR3EthernetController {int receive(mbuf_t p){++rxReceived;assert(p->offset==14&&p->header==p->storage&&p->used==p->length+14);return rxBridge->input(p);}};
// Compile the actual ndrv conversion, preserving unspecified demux union bytes.
#define DLIL_DESC_ETYPE2 4
#define bzero(p,n) memset(p,0,n)
struct ndrv_demux_desc {uint16_t type,length;union {uint8_t other[28];} data;};
struct ifnet_demux_desc_native {uint32_t type;void *data;uint32_t datalen;};
'''
CASE=r'''
static uint8_t peer[6]={0,9,8,7,6,5};static int peerError,peerSize=6,peerZero,peerMulticast,peerSame,generationChange,dropAfterPeer,preTx,preTxError,peerCalls;
static unsigned socketDelivered;static uint8_t socketFrame[1514];static unsigned socketBytes;
static int liveState(void *,a04_network_state *s){*s=realState;if(dropAfterPeer&&peerCalls)s->associated=0;if(generationChange&&peerCalls)++s->association_generation;return stateError;}
static int actualPeer(void *,uint32_t type,uint32_t value,void *p,uint32_t *bytes){assert(type==9&&!value&&*bytes==6);++peerCalls;if(peerError)return peerError;memcpy(p,peer,6);if(peerZero)memset(p,0,6);if(peerMulticast)((uint8_t*)p)[0]|=1;if(peerSame)memcpy(p,realState.mac,6);*bytes=peerSize;return 0;}
static int controlTx(void *,void *p){++preTx;if(preTxError)return preTxError;assert(heldLocks==1);mbuf_freem((mbuf_t)p);return 0;}
static void associated(){realState.firmware_running=realState.control_ready=realState.radio_enabled=realState.associated=realState.preauth_ready=1;realState.station_phase=A04_NET_PHASE_ASSOCIATED;realState.association_generation=17;realState.datapath_ready=realState.authorized=0;}
static unsigned makeFrame(uint8_t *p,bool tx,unsigned body=95,unsigned packetType=3,unsigned ethertype=0x888e){
 memcpy(p,tx?peer:realState.mac,6);memcpy(p+6,tx?realState.mac:peer,6);p[12]=ethertype>>8;p[13]=ethertype;p[14]=2;p[15]=packetType;p[16]=body>>8;p[17]=body;memset(p+18,0x5a,body);return 18+body;
}
static Packet packet(const uint8_t *frame,unsigned bytes,bool tx){Packet p={false};memcpy(p.storage,frame,bytes);p.used=bytes;p.length=tx?bytes:bytes-14;p.offset=tx?0:14;p.header=tx?nullptr:p.storage;return p;}
static void ndrvListener(ifnet_t ifp,mbuf_t m){
 assert(ifp->hdrlen==14 && ifp->addrlen==6);
 // Model the actual demux registration: family0x8021ec, EtherType888e/88c7.
 const uint8_t *h=(const uint8_t*)mbuf_pkthdr_header(m);assert(h&&m->offset==14);
 uint8_t wire[64];memset(wire,0xa5,sizeof(wire));wire[0]=wire[32]=4;wire[1]=wire[3]=wire[33]=wire[35]=0;wire[2]=wire[34]=2;wire[4]=wire[36]=0x88;wire[5]=0x8e;wire[37]=0xc7;
 a04_native_eapol_demux parsed;assert(!a04_native_eapol_demux_decode(1,0x8021ec,wire,64,2,&parsed));
 for(unsigned i=0;i<2;i++){ndrv_demux_desc native;memcpy(&native,wire+32*i,32);ifnet_demux_desc_native d;assert(!ndrv_to_ifnet_demux(&native,&d)&&d.type==4&&d.datalen==2);if(!memcmp(h+12,d.data,2)){
  memcpy(socketFrame,h,14);assert(!mbuf_copydata(m,0,mbuf_pkthdr_len(m),socketFrame+14));socketBytes=14+(unsigned)mbuf_pkthdr_len(m);++socketDelivered;return;
 }}assert(false);
}
int main(){
 legacy_main(); // all existing lifecycle/ioctl/media/authorized IP regressions
 for(failMediaLength=1;failMediaLength<=2;failMediaLength++){
  reset();associated();a04_network_backend backend={};backend.state=liveState;A04BR3BSDNetwork failed;IOService owner;
  assert(failed.attach(&owner,&backend,7)==EINVAL&&!current&&!lockCount&&owner.refs==1);
 }failMediaLength=0;
 reset();associated();a04_network_backend b={};b.state=liveState;b.native_get=actualPeer;b.set_admin=admin;b.transmit=tx;b.transmit_eapol=controlTx;
 A04BR3BSDNetwork bridge;IOService service;rxBridge=&bridge;A04BR3EthernetController controller;
 assert(!bridge.attach(&service,&b,7));current->flags|=IFF_UP;assert(!bridge.dispatch(SIOCSIFFLAGS,nullptr));assert(!(current->flags&IFF_RUNNING));inputObserver=ndrvListener;
 uint8_t raw[1514]={};unsigned bytes=makeFrame(raw,true);Packet m=packet(raw,bytes,true);assert(!current->init.output(current,&m)&&m.freed&&preTx==1&&!txCalls&&!(current->flags&IFF_RUNNING));
 bytes=makeFrame(raw,false);assert(!a04br3_network_control_input(&controller,raw,bytes)&&socketDelivered==1&&socketBytes==bytes&&!memcmp(socketFrame,raw,bytes)&&current->refs==1);assert(!(current->flags&IFF_RUNNING)&&!service.number["WLANDataPathReady"]);
 bytes=makeFrame(raw,false,4,0,0x88c7);assert(!a04br3_network_control_input(&controller,raw,bytes)&&socketDelivered==2);
 // Native registrations are independent of authentication; IP/ARP are not.
 for(unsigned type: {0x0800u,0x0806u,0x86ddu,0x8100u}){bytes=makeFrame(raw,true,95,3,type);m=packet(raw,bytes,true);assert(current->init.output(current,&m)==ENETDOWN&&m.freed);bytes=makeFrame(raw,false,95,3,type);m=packet(raw,bytes,false);m.header=m.storage;assert(bridge.input(&m)==ENETDOWN&&m.freed);}
 // All admission predicates fail closed; neither ordinary TX nor NDRV receives.
 bytes=makeFrame(raw,true);
 for(unsigned fault=0;fault<13;fault++){
  associated();peerCalls=0;peerError=peerZero=peerMulticast=peerSame=generationChange=dropAfterPeer=0;peerSize=6;stateError=0;
  switch(fault){case 0:realState.radio_enabled=0;break;case 1:realState.associated=0;break;case 2:realState.control_ready=0;break;case 3:realState.preauth_ready=0;break;case 4:realState.station_phase=A04_NET_PHASE_AUTH;break;case 5:realState.association_generation=0;break;case 6:peerError=ENOTCONN;break;case 7:peerSize=5;break;case 8:peerZero=1;break;case 9:peerMulticast=1;break;case 10:peerSame=1;break;case 11:generationChange=1;break;case 12:dropAfterPeer=1;break;}
  m=packet(raw,bytes,true);int before=preTx;assert(current->init.output(current,&m)!=0&&m.freed&&preTx==before&&!(current->flags&IFF_RUNNING));
 }
 associated();peerCalls=peerError=peerZero=peerMulticast=peerSame=generationChange=dropAfterPeer=0;peerSize=6;
 // Wrong local/peer addresses, packet versions/types, truncated/extra payload.
 for(unsigned fault=0;fault<10;fault++){
  bytes=makeFrame(raw,true);switch(fault){case 0:raw[0]^=2;break;case 1:raw[6]^=2;break;case 2:raw[14]=0;break;case 3:raw[14]=4;break;case 4:raw[15]=4;break;case 5:raw[16]=0xff;break;case 6:--bytes;break;case 7:++bytes;break;case 8:raw[15]=1;break;case 9:raw[15]=2;break;}
  m=packet(raw,bytes,true);int before=preTx;assert(current->init.output(current,&m)!=0&&m.freed&&preTx==before);
 }
 // Ethernet minimum padding is permitted and never treated as secret data.
 bytes=makeFrame(raw,true,0,1);memset(raw+bytes,0xa5,60-bytes);m=packet(raw,60,true);assert(!current->init.output(current,&m)&&m.freed);
 // Scatter/gather TX is copied by KPI, without requiring contiguous header.
 bytes=makeFrame(raw,true);Packet tail={false};m=packet(raw,bytes,true);tail.used=bytes-7;memcpy(tail.storage,raw+7,tail.used);m.used=7;m.segment=&tail;assert(!current->init.output(current,&m)&&m.freed&&tail.freed);
 preTxError=EIO;m=packet(raw,bytes,true);assert(current->init.output(current,&m)==EIO&&m.freed);preTxError=0;
 // Unsafe/non-owned DLIL header and mbuf chains are rejected before input.
 bytes=makeFrame(raw,false);
 for(unsigned fault=0;fault<7;fault++){
  m=packet(raw,bytes,false);m.header=m.storage;switch(fault){case 0:m.header=nullptr;break;case 1:m.header=(void*)1;break;case 2:m.offset=13;break;case 3:m.flags=0;break;case 4:m.length=1501;break;case 5:m.used=18;break;case 6:m.header=(void*)(UINTPTR_MAX-3);break;}
  int before=inputCalls;assert(bridge.input(&m)!=0&&m.freed&&inputCalls==before);
 }
 Packet other={false};m=packet(raw,bytes,false);m.header=m.storage;m.nextpkt=&other;assert(bridge.input(&m)==EINVAL&&m.freed&&other.freed);
 // Production adapter: invalid input never allocates or hands off; all errors
 // consume owned mbufs once. Controller absence preserves borrowed bytes.
 assert(a04br3_network_control_input(nullptr,raw,bytes)==EINVAL);assert(a04br3_network_control_input(&controller,nullptr,bytes)==EINVAL);
 for(unsigned n=0;n<18;n++)assert(a04br3_network_control_input(&controller,raw,n)==EINVAL);
 assert(a04br3_network_control_input(&controller,raw,1515)==EINVAL);
 assert(a04br3_network_control_input(&controller,(void*)(UINTPTR_MAX-5),18)==EINVAL);
 for(unsigned fault=0;fault<4;fault++){rxAllocError=rxCopyError=rxShortHeader=rxAllocNull=0;switch(fault){case 0:rxAllocError=ENOMEM;break;case 1:rxCopyError=EIO;break;case 2:rxShortHeader=1;break;case 3:rxAllocNull=1;break;}assert(a04br3_network_control_input(&controller,raw,bytes)!=0);}rxAllocError=rxCopyError=rxShortHeader=rxAllocNull=0;
 inputError=EIO;assert(a04br3_network_control_input(&controller,raw,bytes)==EIO&&current->refs==1);inputError=0;
 failInputReference=ENXIO;int beforeInput=inputCalls;assert(a04br3_network_control_input(&controller,raw,bytes)==ENXIO&&inputCalls==beforeInput&&current->refs==1);failInputReference=0;
 for(unsigned offset: {0u,6u}){bytes=makeFrame(raw,false);raw[offset]^=2;beforeInput=inputCalls;assert(a04br3_network_control_input(&controller,raw,bytes)==EACCES&&inputCalls==beforeInput);}bytes=makeFrame(raw,false);
 // Admin down is not bypassed; no fabricated link flag. Authorized IP remains
 // on the ordinary callback, while control continues via its dedicated one.
 current->flags&=~IFF_UP;assert(!bridge.dispatch(SIOCSIFFLAGS,nullptr));m=packet(raw,bytes,false);m.header=m.storage;assert(bridge.input(&m)==ENETDOWN&&m.freed);
 current->flags|=IFF_UP;assert(!bridge.dispatch(SIOCSIFFLAGS,nullptr));realState.datapath_ready=realState.authorized=1;assert(!bridge.refresh()&&(current->flags&IFF_RUNNING));
 bytes=makeFrame(raw,true,95,3,0x0800);m=packet(raw,bytes,true);int old=txCalls;assert(!current->init.output(current,&m)&&txCalls==old+1&&m.freed);
 bytes=makeFrame(raw,true);m=packet(raw,bytes,true);old=txCalls;assert(!current->init.output(current,&m)&&txCalls==old&&m.freed);
 // Real asynchronous detach may complete inside DLIL input; the bridge's
 // temporary ifnet reference must survive until input returns, then release.
 realState.datapath_ready=realState.authorized=0;bytes=makeFrame(raw,false);
 inputObserver=[](ifnet_t p,mbuf_t packet){ndrvListener(p,packet);inlineDetach=true;assert(!rxBridge->detach()&&p->refs==1);};
 assert(!a04br3_network_control_input(&controller,raw,bytes)&&!current&&bridge.isDetached());inputObserver=nullptr;assert(!bridge.finish()&&service.refs==1&&!lockCount);
 // Missing dedicated backend callbacks never fall back to ordinary IP TX.
 for(unsigned missing=0;missing<2;missing++){
  reset();associated();A04BR3BSDNetwork absent;IOService owner;b.native_get=missing?nullptr:actualPeer;b.transmit_eapol=missing?controlTx:nullptr;
  assert(!absent.attach(&owner,&b,7));current->flags|=IFF_UP;assert(!absent.dispatch(SIOCSIFFLAGS,nullptr));bytes=makeFrame(raw,true);m=packet(raw,bytes,true);int ordinary=txCalls;assert(current->init.output(current,&m)==EOPNOTSUPP&&m.freed&&txCalls==ordinary);cleanup(absent,owner);
 }
 puts("PASS actual BSD preauth/control TX + raw RX adapter + NDRV registered demux/header14 + IP/lifetime/failure regressions");return 0;
}
'''
class ControlFrame(unittest.TestCase):
    def test_actual_bsd_runtime_and_native_demux(self):
        ndrv=((ROOT.parent/'host-fixture')/'bsd/net/ndrv.c').read_text()
        a=ndrv.index('int\nndrv_to_ifnet_demux(');b=ndrv.index('\n\nint\nndrv_delspec(',a)
        convert=ndrv[a:b].replace('struct ifnet_demux_desc*','struct ifnet_demux_desc_native*')
        runtime=(ROOT/'A04BR3NetworkRuntime.cpp').read_text();runtime=runtime[runtime.index('int a04br3_network_control_input('):]
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'stub.h').write_text(base.STUB)
            for name in ['IOKit/IOLib.h','IOKit/IOService.h','net/kpi_interface.h','sys/systm.h','sys/sockio.h','sys/kpi_mbuf.h','sys/kauth.h','sys/kern_event.h','libkern/OSAtomic.h','kern/thread.h','net/if.h','net/net_kev.h','net/if_dl.h','net/if_types.h','net/if_ether.h','net/ethernet.h','net/if_media.h']:
                f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text('#include "stub.h"\n')
            (p/'driver.cpp').write_bytes((ROOT/'A04BR3BSDNetwork.cpp').read_bytes())
            legacy=base.CASE;end=legacy.rfind('}');legacy=legacy[:end]+'return 0;\n'+legacy[end:]
            (p/'case.cpp').write_text('#define main legacy_main\n'+legacy+'\n#undef main\n'+EXTRA+'\n#include "A04BR3NativeEAPOLProtocol.h"\n'+convert+'\n'+runtime+'\n'+CASE)
            subprocess.run(['c++','-std=c++14','-Wall','-Wextra','-Werror','-Wno-misleading-indentation','-Wno-unused-variable','-fsanitize=address,undefined','-fno-sanitize-recover=all','-no-pie','-I',str(p),'-I',str(ROOT),str(p/'case.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
    def test_arm64_control_policy(self):
        with tempfile.TemporaryDirectory() as directory:
            p=Path(directory);(p/'sys').mkdir();(p/'sys/errno.h').write_text('#define EINVAL 22\n#define ENETDOWN 50\n#define EACCES 13\n#define EPROTONOSUPPORT 43\n')
            (p/'caller.c').write_text('#include "A04BR3NetworkControlFrame.h"\nint check(const void *p,unsigned n,const uint8_t *a,const uint8_t *b,const struct a04_network_state *s){return a04_network_control_associated(s)?a04_network_control_frame(p,n,a,b,1):ENETDOWN;}\n')
            subprocess.run(['clang','--target=arm64-apple-ios16.0','-std=c11','-ffreestanding','-O2','-Wall','-Wextra','-Werror','-I',str(p),'-I',str(ROOT),'-c',str(p/'caller.c'),'-o',str(p/'arm.o')],check=True)
if __name__=='__main__':unittest.main()
