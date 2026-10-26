"""実ソースの keypad/Consumer callback をホストで実行する。実機 MMIO は行わない。"""
from pathlib import Path
import os
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class ButtonsTest(unittest.TestCase):
    def test_physical_edges_reports_and_lifetime(self):
        source = (ROOT / 'package/A04BR3Input.cpp').read_text()
        # 単独 device checkout では対応する NativeHidInput ソースを明示できる。
        hid = Path(os.environ.get('NATIVE_HID_INPUT_SOURCE',
                   str(ROOT.parent / 'UniXNU/drivers/NativeHidInput/NativeHidInput.cpp')))
        bridge = hid.read_text()
        ownership = source[source.index('IOLock *gButtonLock;'):source.index('\nuint32_t rd(')]
        setter = source[source.index('void native_buttons_set_sink(', source.index('// Only this board')):
                        source.index('\nvoid a04br3_input_start', source.index('// Only this board'))]
        keypad = source[source.index('void keyThread('):source.index('// a04br3-input-selftest=')]
        action = bridge[bridge.index('    static IOReturn buttonsAction('):
                        bridge.index('    bool hasEventDriverCached()')]
        tick = bridge[bridge.index('    static void tick('):bridge.index('\npublic:', bridge.index('    static void tick('))]
        harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <cstring>
#include <mutex>
#include <thread>
#include <vector>
#include <condition_variable>
using IOLock = std::mutex;
thread_local unsigned lockDepth;
IOLock *IOLockAlloc() { return new IOLock; }
void IOLockFree(IOLock *p) { delete p; }
void IOLockLock(IOLock *p) { p->lock(); ++lockDepth; }
void IOLockUnlock(IOLock *p) { --lockDepth; p->unlock(); }
struct OSObject {
    std::atomic<unsigned> refs{1};
    virtual ~OSObject() = default;
    void retain() { assert(refs.fetch_add(1)); }
    void release() { assert(refs.fetch_sub(1)); }
};
''' + ownership + setter + r'''
using IOReturn = int;
constexpr int kIOReturnSuccess=0, kIOReturnNoMemory=-1, kIODirectionNone=0;
#define OSDynamicCast(T,p) dynamic_cast<T *>(p)
#define OSSafeReleaseNULL(p) do { delete p; p=nullptr; } while(0)
bool failAllocation=false, failReport=false;
struct IOBufferMemoryDescriptor {
    unsigned char report[2];
    static IOBufferMemoryDescriptor *withBytes(const void *p, unsigned n, int) {
        assert(n==2); if(failAllocation)return nullptr;
        auto *b=new IOBufferMemoryDescriptor;
        std::memcpy(b->report,p,2); return b;
    }
};
struct IOTimerEventSource { void setTimeoutMS(unsigned) {} };
std::vector<unsigned> reports;
bool requestHome;
void native_hid_input_observed(unsigned,int) {}
void native_hid_live_checkpoint(OSObject *) {}
bool native_hid_input_requested() { bool r=requestHome; requestHome=false; return r; }
struct NativeHidInput : OSObject {
    bool physicalHome=false, buttonsActive=true, down=false, touchOnly=false;
    bool buttonsPending=false, attached=true;
    unsigned char buttons=0;
    bool hasEventDriverCached() { return attached; }
    static bool hasEventDriver(OSObject *p,unsigned) { return static_cast<NativeHidInput *>(p)->attached; }
    int handleReport(IOBufferMemoryDescriptor *b,int,int) {
        assert(!lockDepth); assert(b->report[0]==2);
        if(failReport)return kIOReturnNoMemory;
        reports.push_back(b->report[1]); return 0;
    }
''' + action + tick + r'''
};
void receive(void *p,uint32_t mask) {
    assert(!lockDepth);
    NativeHidInput::buttonsAction(static_cast<NativeHidInput *>(p),
        reinterpret_cast<void *>(uintptr_t(mask)),nullptr,nullptr,nullptr);
}
using wait_result_t=int;
volatile uint32_t a04br3_input_stage, a04br3_key_presses;
std::vector<unsigned> samples;
unsigned sampleIndex;
struct End {};
void kwr(unsigned,unsigned) {}
unsigned krd(unsigned offset) { assert(offset==4); return ~samples.at(sampleIndex); }
void IOLog(const char *,...) {}
void IOSleep(unsigned ms) { assert(ms==10); if (++sampleIndex==samples.size()) throw End{}; }
''' + keypad + r'''
std::mutex callbackMutex;
std::condition_variable callbackCv;
bool entered=false, proceed=false;
void blockedReceive(void *p,uint32_t) {
    assert(!lockDepth);
    auto *object=static_cast<OSObject *>(p);
    std::unique_lock<std::mutex> l(callbackMutex);
    entered=true; callbackCv.notify_all();
    callbackCv.wait(l,[]{return proceed;});
    assert(object->refs.load()==2); // caller + in-flight; registration was removed
}
int main() {
    NativeHidInput device; device.physicalHome=true;
    native_buttons_set_sink(receive,&device);
    // Debounce bounce, long Home hold, overlap Volume Up, release Home first.
    samples={0,0,0,2,0,2,2,2,2,2,2,3,3,3,1,1,1,0,0,0};
    try { keyThread(nullptr,0); } catch (End &) {}
    assert((reports==std::vector<unsigned>{0,1,5,4,0}));
    assert(a04br3_key_presses==1);
    // A replacement sink receives the held set even with no new physical edge.
    publishButtons(2);
    NativeHidInput next; next.physicalHome=true;
    native_buttons_set_sink(receive,&next);
    assert(device.refs==1 && next.refs==2);
    publishButtons(2); assert(reports.back()==1);
    unsigned n=reports.size(); publishButtons(2); assert(reports.size()==n);
    // Hold received before the event driver attaches is replayed without a new edge.
    IOTimerEventSource timer;
    next.attached=false; publishButtons(0); publishButtons(2);
    n=reports.size(); NativeHidInput::tick(&next,&timer); assert(reports.size()==n);
    next.attached=true; NativeHidInput::tick(&next,&timer);
    assert(reports.size()==n+1 && reports.back()==1 && !next.buttonsPending);
    // A synthetic Home release must not release a physical Home hold.
    requestHome=true; NativeHidInput::tick(&next,&timer); assert(next.down && reports.back()==1);
    NativeHidInput::tick(&next,&timer); assert(!next.down && reports.back()==1);
    publishButtons(0); assert(reports.back()==0);
    // Physical release failures must retry without needing another edge.
    for(unsigned failure=0;failure<2;++failure) {
        publishButtons(2); assert(reports.back()==1);
        failAllocation=failure==0; failReport=failure==1;
        publishButtons(0);
        assert(next.buttonsPending && reports.back()==1);
        NativeHidInput::tick(&next,&timer);
        assert(next.buttonsPending && reports.back()==1);
        failAllocation=failReport=false;
        NativeHidInput::tick(&next,&timer);
        assert(!next.buttonsPending && reports.back()==0);
    }
    // Unqualified boards preserve the old mask (no physical Home).
    NativeHidInput baseline;
    NativeHidInput::buttonsAction(&baseline,reinterpret_cast<void *>(15),nullptr,nullptr,nullptr);
    assert(reports.back()==14);
    // A queued callback after stop does not dispatch a HID report.
    NativeHidInput::buttonsStopAction(&next,nullptr,nullptr,nullptr,nullptr);
    n=reports.size(); publishButtons(2); assert(reports.size()==n);
    // Unregister cannot free a callback context that is currently in flight.
    native_buttons_set_sink(blockedReceive,&next);
    std::thread producer([]{publishButtons(2);});
    { std::unique_lock<std::mutex> l(callbackMutex); callbackCv.wait(l,[]{return entered;}); }
    native_buttons_set_sink(nullptr,nullptr);
    assert(next.refs==2);
    { std::lock_guard<std::mutex> l(callbackMutex); proceed=true; }
    callbackCv.notify_all(); producer.join(); assert(next.refs==1);
    delete gButtonLock;
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-buttons-') as tmp:
            cpp = Path(tmp) / 'buttons.cpp'
            binary = Path(tmp) / 'buttons'
            cpp.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-O1', '-g', '-pthread', '-Wall', '-Wextra',
                            '-Werror', '-fsanitize=address,undefined', str(cpp), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
