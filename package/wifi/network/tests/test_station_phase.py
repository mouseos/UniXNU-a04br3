# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Map the REAL SDIO station structure, including all intermediate join phases."""
from pathlib import Path
import subprocess,tempfile,unittest
ROOT=Path(__file__).resolve().parents[1]
class StationPhase(unittest.TestCase):
 def test_actual_station_phase_mapping(self):
  code=r'''
#include "A04BR3StationPhase.h"
#include <assert.h>
int main(){
 a04_wlan_station s={};
 assert(a04_network_phase_from_station(nullptr)==A04_NET_PHASE_UNKNOWN);
 assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_UNKNOWN);
 s.configured=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_IDLE);
 s.scan.pending=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_SCAN);s.scan.pending=0;
 s.selected=s.channel_pending=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_UNKNOWN);
 s.channel_pending=0;s.join.phase=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_AUTH);
 for(unsigned phase=2;phase<=4;phase++){s.join.phase=phase;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_ASSOC);}
 s.sta_active=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_ASSOCIATED);
 s.failed=1;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_UNKNOWN);
 s.failed=0;s.sta_active=0;s.join.phase=99;assert(a04_network_phase_from_station(&s)==A04_NET_PHASE_UNKNOWN);
}
'''
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'test.cpp').write_text(code)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
   subprocess.run([str(p/'test')],check=True)
 def test_actual_sdio_snapshot_producer(self):
  sdio=ROOT.parent/'sdio/A04BR3SDIO.cpp'
  text=sdio.read_text();start=text.index(' static int backendState(');end=text.index(' static int backendGet(',start)
  body=text[start:end]
  code=r'''
#include "A04BR3StationPhase.h"
#include "../sdio/A04BR3WLANRuntime.h"
#include "../sdio/A04BR3WLANNetworkProfile.h"
#include <assert.h>
#include <strings.h>
#include <errno.h>
static bool locked;static void IOLockLock(void *){assert(!locked);locked=true;}
static void IOLockUnlock(void *){assert(locked);locked=false;}
class A04BR3SDIO {
public:
 void *transportLock=nullptr;bool identityReady=true,networkInitialized=true,liveStopping=false;
 struct {unsigned ready;} wlanDownload={1};
 a04_wlan_host host={};a04_wlan_host *wlanHost=&host;
 struct Live {a04_wlan_runtime runtime;};Live storage={};Live *live=&storage;
 struct {void (*packet)();} nativeClient={};
PRODUCER
};
int main(){
 A04BR3SDIO d;auto &r=d.live->runtime;r.configured=r.mac_valid=1;r.mac[0]=2;r.station.configured=1;
 a04_network_state out={};
 assert(!d.backendState(&d,&out) && out.control_ready && out.station_phase==A04_NET_PHASE_IDLE && !out.associated && out.join_state==A04_NET_JOIN_NOT_STARTED);
 r.station.local.permitted_channels=(1u<<1)|(1u<<6);
 assert(!d.backendState(&d,&out) && out.permitted_channels==r.station.local.permitted_channels);
 r.station.scan.pending=1;assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_SCAN);
 r.station.scan.pending=0;r.station.selected=1;r.station.join.phase=1;
 assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_AUTH && !out.associated && out.join_state==A04_NET_JOIN_PENDING);
 r.station.join.phase=3;assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_ASSOC && !out.associated);
 r.station.join.phase=4;assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_ASSOC && !out.associated);
 r.station.join.aid=42;r.station.sta_active=1;assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_ASSOCIATED && out.associated && !out.datapath_ready && out.join_state==A04_NET_JOIN_OPEN_COMPLETE);
 r.station.secured=1;assert(!d.backendState(&d,&out) && out.associated && out.join_state==A04_NET_JOIN_UNKNOWN);r.station.secured=0;
 r.failed=1;assert(!d.backendState(&d,&out) && !out.control_ready && !out.permitted_channels && out.station_phase==A04_NET_PHASE_UNKNOWN && out.join_state==A04_NET_JOIN_UNKNOWN);
 r.failed=0;d.liveStopping=true;assert(!d.backendState(&d,&out) && out.station_phase==A04_NET_PHASE_UNKNOWN && !out.permitted_channels);
 d.live=nullptr;assert(!d.backendState(&d,&out) && !out.present && out.station_phase==A04_NET_PHASE_UNKNOWN && !locked);
}
'''.replace('PRODUCER',body)
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp);(p/'producer.cpp').write_text(code)
   subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-I',str(ROOT),str(p/'producer.cpp'),'-o',str(p/'producer')],check=True)
   subprocess.run([str(p/'producer')],check=True)
if __name__=='__main__':unittest.main()
