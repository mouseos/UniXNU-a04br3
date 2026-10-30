#!/usr/bin/env python3
"""Cross-compile the actual capture worker with minimal freestanding shims."""
from pathlib import Path
import subprocess,tempfile
root=Path(__file__).resolve().parents[2]
s=(root/'package/A04BR3AutoReset.cpp').read_text()
a=s.index('struct RtcCaptureIO {'); b=s.index('\n#endif\n\nvoid cycleThread',a)
pre='''#include "A04BR3RtcWacs.h"\n#include "A04BR3RtcReference.h"\n#include "A04BR3WatchdogGate.h"\nstatic A04BR3WatchdogGate gWatchdogGate={}; static uint32_t gRtcCaptureActive;\nusing wait_result_t=int;\nextern volatile unsigned char *gPwrap;\nextern volatile uint32_t a04br3_runtime_probe_active,a04br3_runtime_recovery_ready;\nconstexpr unsigned kWacsRdata=0xa4,kWacsCmd=0xa0,kWacsVldclr=0xa8;\nextern bool a04br3_pwrap_try_lock(); extern void a04br3_pwrap_unlock();\nextern uint32_t rd(volatile uint8_t*,unsigned); extern void wr(volatile uint8_t*,unsigned,uint32_t);\nextern unsigned panic_active(); extern void IOSleep(unsigned); extern int current_thread(); extern void thread_terminate(int); extern void IOLog(const char*,...);\n'''
with tempfile.TemporaryDirectory(prefix='rtc-arm64-') as d:
 p=Path(d)/'arm.cpp';o=Path(d)/'arm.o';p.write_text(pre+s[a:b]+'\nvoid use_capture() { rtcCaptureThread(nullptr,0); }\n')
 subprocess.run(['clang++','-target','arm64-apple-ios17.0','-ffreestanding','-std=c++17','-Wall','-Wextra','-Werror','-I',str(root/'package'),'-c',str(p),'-o',str(o)],check=True)
print('PASS actual worker arm64 Apple-target compile with shims; XNU SDK and kernel link untested')
