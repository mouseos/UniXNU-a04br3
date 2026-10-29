// SPDX-License-Identifier: MIT
#include "../../package/powervr/NativePowerVRSubmitStage.h"
#include <cassert>
#include <thread>
#include <vector>
int main() {
 NativeSubmitStage s={}; NativeSubmitSample a={}; a.pid=57;a.tid=1165;a.first=100;a.last=200;
 a.ticks[NativeSubmitTotal]=100;a.ticks[NativeSubmitKick]=80;a.ticks[NativeSubmitKccb]=70;
 native_submit_stage_record(&s,a); NativeSubmitRow out[2]={}; assert(native_submit_stage_take(&s,out)==1);
 assert(out[0].count==1 && out[0].sample.ticks[NativeSubmitKick]==80 && out[0].sample.ticks[NativeSubmitKccb]==70);
 assert(native_submit_stage_take(&s,out)==0);
 a.first=201;a.last=301;native_submit_stage_record(&s,a);
 bool found=false;for(int i=0;i<8;i++) {unsigned n=native_submit_stage_take(&s,out);for(unsigned j=0;j<n;j++) if(out[j].sample.tid==1165) {found=true;assert(out[j].count==2 && out[j].sample.first==100 && out[j].sample.last==301);}}
 assert(found);
 NativeSubmitSample bad=a; bad.last=0;native_submit_stage_record(&s,bad);assert(s.rows[0].count==2);
 for(unsigned i=0;i<15;i++) {NativeSubmitSample b=a;b.tid=2000+i;native_submit_stage_record(&s,b);}
 NativeSubmitSample extra=a;extra.tid=9999;native_submit_stage_record(&s,extra);assert(s.full==1);
 s.lock=1;native_submit_stage_record(&s,a);assert(s.busy==1);assert(native_submit_stage_take(&s,out)==0);assert(s.reportBusy==1);s.lock=0;
 NativeSubmitStage parallel={};std::vector<std::thread> workers;
 for(unsigned i=0;i<4;i++) workers.emplace_back([&,i]{NativeSubmitSample v=a;v.tid=i+1;for(int j=0;j<10000;j++) native_submit_stage_record(&parallel,v);});
 for(auto &t:workers) t.join();
 uint64_t count=0;
 for(auto &r:parallel.rows) count+=r.count;
 assert(count+parallel.busy==40000);assert(!parallel.full);
}
