# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual BSD bridge link events, error retry and reentrant lifetime (ASan/UBSan).
Shared ifnet and KPI callbacks are host simulated. No real DHCP lease claim.
"""
import unittest
import test_bsd_network as fixture

class LinkPublication(unittest.TestCase):
 def test_real_bridge_transitions_failures_and_reentry(self):
  saved=fixture.CASE
  prefix=saved.split('int main(){')[0]
  fixture.CASE=prefix+r'''
static A04BR3BSDNetwork *activeBridge;static IOService *activeService;
static void during_link(){
 assert(current->refs>=2&&!heldLocks);
 assert(activeBridge->detach()==EBUSY); // no detach/free during DLIL callback
 assert(activeBridge->finish()==EBUSY);
 // A synchronous consumer can disable admin while LINK_ON is being delivered.
 current->flags&=~IFF_UP;
 assert(current->init.ioctl(current,SIOCSIFFLAGS,nullptr)==EBUSY);
 eventHook=nullptr;
}
int main(){
 mediaCases();traceCases();reset();
 a04_network_backend b={};b.state=state;b.native_get=get;b.set_admin=admin;b.transmit=tx;b.native_set=set;
 A04BR3BSDNetwork n;IOService s;activeBridge=&n;activeService=&s;
 realState.firmware_running=realState.control_ready=1;assert(!n.attach(&s,&b,7));
 assert(!linkUps&&!linkDowns&&current->refs==1);
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 assert(!linkUps&&!(current->flags&IFF_RUNNING)); // association/keyless preauth no link
 ready();eventError=EIO;
 assert(n.refresh()==EIO&&!linkUps&&!(current->flags&IFF_RUNNING)&&current->refs==1);
 eventError=0;assert(!n.refresh()&&linkUps==1&&(current->flags&IFF_RUNNING));
 for(unsigned i=0;i<4;i++)assert(!n.refresh());assert(linkUps==1); // suppress duplicates
 realState.associated=realState.datapath_ready=0;eventError=EIO;
 assert(n.refresh()==EIO&&!linkDowns&&!(current->flags&IFF_RUNNING)&&current->refs==1);
 eventError=0;assert(!n.refresh()&&linkDowns==1);assert(!n.refresh()&&linkDowns==1);
 // Coalesced ready->new-ready epoch requires LINK_OFF then LINK_ON.
 ready();realState.association_generation=17;assert(!n.refresh()&&linkUps==2);
 realState.association_generation=18;assert(!n.refresh()&&linkDowns==2&&linkUps==3);
 realState.associated=realState.datapath_ready=0;assert(!n.refresh()&&linkDowns==3);
 linkUps=linkDowns=1; // below tests count independent success pairs
 // Rejoin must generate the next real pair, not reset or reuse prior state.
 ready();failFlags=EIO;assert(n.refresh()==EIO&&linkUps==1&&!(current->flags&IFF_RUNNING));failFlags=0;
 failInputReference=EIO;assert(n.refresh()==EIO&&linkUps==1&&current->refs==1);failInputReference=0;
 assert(!n.refresh()&&linkUps==2);stateError=ENXIO;
 assert(n.refresh()==ENXIO&&linkDowns==2&&!(current->flags&IFF_RUNNING));stateError=0;
 eventHook=during_link;assert(!n.refresh()); // LINK_ON callback cancels admin: symmetric LINK_OFF
 assert(linkUps==3&&linkDowns==3&&!(current->flags&IFF_RUNNING)&&current->refs==1);
 // Admin still down even though radio datapath snapshot is ready.
 assert(!n.refresh()&&linkUps==3);
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr)&&linkUps==4);
 eventError=EIO;assert(n.detach()==EIO&&!n.isDetached()&&current->attached&&!(current->flags&IFF_RUNNING));
 eventError=0;assert(!n.detach()&&linkDowns==4);completeDetach();assert(!n.finish());
 assert(!current&&!lockCount&&s.refs==1);
 return 0;
}
'''
  try:fixture.BSDNetwork('test_actual_bridge').test_actual_bridge()
  finally:fixture.CASE=saved

if __name__=='__main__':unittest.main()
