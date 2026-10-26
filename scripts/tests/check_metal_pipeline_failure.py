#!/usr/bin/env python3
"""Actual bounded callback/board serializer; host-only, no Apple/GPU claim."""
from pathlib import Path
import os, subprocess, tempfile
ROOT=Path(__file__).resolve().parents[2]
FRONT=(ROOT.parent/'UniXNU/drivers/NativeMetal/NativeBridgeMetalFrontend.mm').read_text()
BOARD=(ROOT/'package/powervr/user/NativePowerVRMetalBoard.mm').read_text()
def function(source, signature):
 start=source.index(signature);p=source.index('{',start);depth=1;end=p+1
 while depth:
  depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]
h=r'''
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstring>
#include <string>
#include <vector>
#include <thread>
using NativeMetalPipelineFailureCallback=void(*)(unsigned,const char*,const char*,const char*);
static std::atomic<NativeMetalPipelineFailureCallback> gPipelineFailureCallback{nullptr};
static std::atomic<unsigned> gPipelineFailureReports{0};
static std::vector<std::string> rows;
void native_metal_board_text(const char *s){assert(strlen(s)<=63);rows.emplace_back(s);}
'''
for src,sig in [(FRONT,'extern "C" void native_bridge_metal_set_pipeline_failure_callback('),
                (FRONT,'static NativeMetalPipelineFailureCallback claimPipelineFailure('),
                (BOARD,'void pipelineFailureField('),(BOARD,'void pipelineFailure(')]:
 h+=function(src,sig)+'\n'
h+=r'''
int main(){
 unsigned sequence=99;
 assert(!claimPipelineFailure(&sequence)&&sequence==99&&!gPipelineFailureReports.load());
 native_bridge_metal_set_pipeline_failure_callback(pipelineFailure);
 std::atomic<unsigned> claimed{0},mask{0};std::vector<std::thread> threads;
 for(unsigned i=0;i<16;i++)threads.emplace_back([&]{unsigned n=0;auto cb=claimPipelineFailure(&n);if(cb){claimed++;mask.fetch_or(1u<<n);}});
 for(auto &t:threads){t.join();}
 assert(claimed==2&&mask==6&&gPipelineFailureReports==2);
 // Re-registering must not reopen the per-process budget.
 native_bridge_metal_set_pipeline_failure_callback(pipelineFailure);assert(!claimPipelineFailure(&sequence));
 pipelineFailure(1,"descriptor","foreign function","label=CA v=<missing/foreign>");
 assert(rows[0]=="PF1 S00 descriptor"&&rows[1]=="PF1 E00 foreign function");
 rows.clear();std::string error(600,'E'),spec(450,'D');pipelineFailure(2,"runtime",error.c_str(),spec.c_str());
 std::string gotE,gotD;
 for(auto &r:rows){if(r.rfind("PF2 E",0)==0&&r.find("truncated")==std::string::npos)gotE+=r.substr(8);
                  if(r.rfind("PF2 D",0)==0&&r.find("truncated")==std::string::npos)gotD+=r.substr(8);}
 assert(gotE==error.substr(0,512)&&gotD==spec.substr(0,384));
 assert(rows.size()==22&&rows[12]=="PF2 E truncated at 512"&&rows.back()=="PF2 D truncated at 384");
}
'''
# Check the production failure sites and the original-error lifetime, not a model.
method=function(FRONT,'- (id<MTLRenderPipelineState>)newRenderPipelineStateWithDescriptor:(MTLRenderPipelineDescriptor *)d options:')
for stage in ('descriptor','archives','runtime'):assert f'"{stage}"' in method
assert method.index('NSString *runtimeFailure=handle?nil:lastError();')<method.index('journalRenderPipeline(')
assert 'nil,runtimeFailure)' in method and 'handle?nullptr:nmb_last_error()' not in method
report=function(FRONT,'static void reportPipelineFailure(')
assert report.index('claimPipelineFailure(')<report.index('snprintf(')
assert 'gProblemLock' not in report
assert 'noteProblem' not in report
assert 'PVR_METAL_PIPELINE_DIAG' in BOARD and '!strcmp(pipelineDiag,"1")' in BOARD
with tempfile.TemporaryDirectory(prefix='metal-pipeline-failure-') as t:
 src=Path(t)/'fixture.cpp';src.write_text(h);binary=src.with_suffix('')
 subprocess.run([os.environ.get('CXX','c++'),'-std=c++17','-Wall','-Wextra','-Werror','-pthread','-fsanitize=address,undefined','-fno-omit-frame-pointer',str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True,timeout=30)
print('PASS actual callback default-null, concurrent first-two budget, no rearm, board chunk/limit/truncation; original-error capture precedes journal; descriptor/archive/runtime hooks (ASan/UBSan)')
print('Apple Objective-C++ frontend/board compilation remains required; no hardware claim.')
