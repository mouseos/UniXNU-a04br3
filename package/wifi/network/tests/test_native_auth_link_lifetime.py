# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Actual BSD/Apple adapter/Rendezvous cancellation, with host framework/state.
No handshake success, radio, ACK or actual native process is simulated as proof.
"""
import unittest
import test_ethernet_binding as binding

class NativeAuthLinkLifetime(unittest.TestCase):
 def test_completion_requires_current_published_admin_link(self):
  saved=binding.NATIVE_CASE
  before=r'''
 // A real backend may complete M4 while BSD admin is still down. Crypto/port
 // state alone must not publish a native authentication-complete interest.
 realState.associated=realState.datapath_ready=realState.authorized=realState.auth_profile_valid=1;
 realState.auth_upper=8;realState.association_generation=40;
 assert(!(current->flags&IFF_RUNNING));unsigned beforeAuth=authMessages;
 assert(client.authenticated(client.context,40)==ENETDOWN&&authMessages==beforeAuth);
 realState.associated=realState.datapath_ready=realState.authorized=realState.auth_profile_valid=0;
'''
  after=r'''
 // Cancel the upper port after a fresh association, before its completion
 // callback. Backend remains genuinely authorized; BSD admin owns LINK_OFF.
 realState.association_generation=42;beforeAuth=authMessages;
 current->flags&=~IFF_UP;
 assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 assert(!(current->flags&IFF_RUNNING));
 assert(client.authenticated(client.context,42)==ENETDOWN&&authMessages==beforeAuth);
 Packet cancelledTX={false};int oldTX=txCalls,oldRX=inputCalls;
 assert(current->init.output(current,&cancelledTX)==ENETDOWN&&cancelledTX.freed&&txCalls==oldTX);
 auto *liveController=mockAttachedInterface;assert(liveController);
 // Actual controller -> interface -> BSD RX consumes on this failure too.
 auto *nativeOwner=dynamic_cast<A04BR3NativeNetwork *>(owner);assert(nativeOwner);
 uint8_t cancelFrame[14]={};int frees=packetFrees;
 client.packet(client.context,1,cancelFrame,sizeof(cancelFrame));
 assert(packetFrees==frees+1&&inputCalls==oldRX);
 current->flags|=IFF_UP;assert(!current->init.ioctl(current,SIOCSIFFLAGS,nullptr));
 assert(!client.authenticated(client.context,42)&&authMessages==beforeAuth+1);
 assert(!client.authenticated(client.context,42)&&authMessages==beforeAuth+1);
'''
  binding.NATIVE_CASE=saved.replace(' // Real authenticated callback:',before+'\n // Real authenticated callback:',1).replace(' realState.association_generation=42;assert(client.authenticated(client.context,41)==ENETDOWN);',after+'\n realState.association_generation=43;assert(client.authenticated(client.context,41)==ENETDOWN);',1)
  try:binding.EthernetBinding('test_adapter').test_adapter()
  finally:binding.NATIVE_CASE=saved

if __name__=='__main__':unittest.main()
