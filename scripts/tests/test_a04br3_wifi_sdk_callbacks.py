#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Run extracted production callbacks/event logic with exact API typedefs.

Synthetic collaborators test forwarding and bytes, not full SDK/class ABI link.
Use --xnu-source to compile against the pinned production kern_event_msg fields.
"""
import argparse
from pathlib import Path
import re
import subprocess
import sys
import unittest
from test_a04br3_wifi_import_warnings import compile_code

APPLE = Path(__file__).resolve().parents[2] / 'package/wifi/network/apple'
XNU_SOURCE = None
FLAGS = ('-Wcast-function-type-strict', '-Wdeprecated-volatile', '-Wunused-but-set-variable',
         '-Wgnu-variable-sized-type-not-at-end')
BASE = '''using UInt32 = __UINT32_TYPE__; using uint32_t = __UINT32_TYPE__;
using uintptr_t = __UINTPTR_TYPE__; using IOReturn = int;
enum { kIOReturnSuccess = 0, kIOReturnCannotLock = -1 };
static void require(bool ok) { if (!ok) __builtin_trap(); }
#define assert(value) require(value)
#define DLOG(...) ((void)0)
'''

def text(name):
    return (APPLE / name).read_text()


def block(source, offset):
    first = source.index('{', offset)
    depth = 0
    # Ignore braces in comments and literals without changing source offsets.
    clean = re.sub(r'/\*.*?\*/|//[^\n]*|"(?:\\.|[^"\\])*"',
                   lambda m: ' ' * len(m[0]), source, flags=re.S)
    for i in range(first, len(source)):
        if clean[i] == '{': depth += 1
        elif clean[i] == '}':
            depth -= 1
            if not depth: return source[offset:i + 1]
    raise AssertionError('unclosed production block')


def function(name, signature):
    source = text(name)
    return block(source, source.index(signature))


def callback(name, marker):
    source = text(name)
    return block(source, source.index('[](', source.index(marker)))


def action_typedef(name):
    return re.search(r'typedef IOReturn \(\*Action\)\(.*?;', text(name), re.S).group(0)


class SDKCallbacks(unittest.TestCase):
    def check_code(self, code):
        for target in ((), ('--target=arm64-apple-ios16.0',)):
            result = compile_code(code, target=(*FLAGS, *target))
            self.assertEqual(result.returncode, 0, result.stderr)
        result = compile_code(code, runtime=True, target=FLAGS)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_stats_action_exact_seven_argument_type(self):
        adapter = callback('IOOutputQueue.cpp', '_statsData = IONetworkData::withInternalBuffer(')
        dispatch = function('IOOutputQueue.cpp', 'IOReturn\nIOBasicOutputQueue::dispatchNetworkDataNotification(')
        code = BASE + 'struct IONetworkData { ' + action_typedef('IONetworkData.h') + ' };\n'
        code += '''struct IOBasicOutputQueue {
private:
    static IOReturn dispatchNetworkDataNotification(void *,void *,IONetworkData *,UInt32);
public:
    int calls=0; IONetworkData *last=nullptr; UInt32 kind=0; void *param=nullptr;
    IOReturn handleNetworkDataAccess(IONetworkData *d,UInt32 t,void *p) {
        ++calls; last=d; kind=t; param=p; return -123;
    }
    static IONetworkData::Action action() { return ''' + adapter + '''; }
};
''' + dispatch + '''
int main() {
    IOBasicOutputQueue self; IONetworkData data; UInt32 length=77;
    auto action=IOBasicOutputQueue::action();
    void *param=reinterpret_cast<void *>(0x1234);
    require(action(&self,param,&data,0x87654321,nullptr,&length,0xffffffff)==-123);
    require(self.calls==1 && self.last==&data && self.kind==0x87654321 && self.param==param);
    require(length==77);
}
'''
        self.check_code(code)

    def test_gate_action_exact_type_and_cannot_lock(self):
        method = function('IOOutputQueue.cpp', 'void IOGatedOutputQueue::output(')
        code = BASE + '''struct OSObject {}; struct IOMbufQueue {};
struct IOCommandGate {
    using Action=IOReturn (*)(OSObject *,void *,void *,void *,void *);
    OSObject *owner; IOReturn status=0; int calls=0;
    IOReturn attemptAction(Action action,void *a,void *b,void *c,void *d=nullptr) {
        if(status) return status;
        ++calls; return action(owner,a,b,c,d);
    }
};
enum { kStateOutputDeferred=0x100 };
struct IOGatedOutputQueue {
    IOCommandGate *_gate; int calls=0; OSObject *lastOwner=nullptr; IOMbufQueue *lastQueue=nullptr;
private:
    static void gatedOutput(OSObject *owner,IOGatedOutputQueue *self,IOMbufQueue *queue,UInt32 *state) {
        ++self->calls; self->lastOwner=owner; self->lastQueue=queue; *state=42;
    }
public:
    void output(IOMbufQueue *,UInt32 *);
};
''' + method + '''
int main() {
    OSObject owner; IOCommandGate gate{&owner}; IOGatedOutputQueue self{&gate};
    IOMbufQueue queue; UInt32 state=7;
    self.output(&queue,&state);
    require(self.calls==1 && gate.calls==1 && self.lastOwner==&owner && self.lastQueue==&queue && state==42);
    gate.status=kIOReturnCannotLock; state=8; self.output(&queue,&state);
    require(state==kStateOutputDeferred && self.calls==1 && gate.calls==1);
    gate.status=-2; state=9; self.output(&queue,&state);
    require(state==9 && self.calls==1 && gate.calls==1);
}
'''
        self.check_code(code)

    def test_power_action_gate_recursion_and_notice_forwarding(self):
        method = function('IONetworkInterface.cpp', 'void\nIONetworkInterface::powerChangeHandler(')
        notice = block(text('IONetworkInterface.cpp'), text('IONetworkInterface.cpp').index('struct IONetworkPowerChangeNotice')) + ';'
        code = BASE + 'using IOPMPowerFlags=unsigned long; struct IOService {};\n'
        code += 'struct IONetworkController { ' + action_typedef('IONetworkController.h') + '''
    int calls=0; void *client=nullptr;
    IOReturn executeCommand(void *c,Action action,void *target,void *a=nullptr,void *b=nullptr,void *d=nullptr,void *e=nullptr) {
        ++calls; client=c; return action(target,a,b,d,e);
    }
};
''' + notice + '''
enum { kPhasePowerStateWillChange=1, kPhasePowerStateDidChange=2 };
struct IONetworkInterface {
    IONetworkController *_driver; int will=0,did=0; IOPMPowerFlags flags=0; UInt32 state=0; IOService *maker=nullptr;
    void controllerWillChangePowerState(IONetworkController *,IOPMPowerFlags f,UInt32 s,IOService *m) { ++will; flags=f; state=s; maker=m; }
    void controllerDidChangePowerState(IONetworkController *,IOPMPowerFlags f,UInt32 s,IOService *m) { ++did; flags=f; state=s; maker=m; }
    static void powerChangeHandler(void *,void *,void *);
};
''' + method + '''
int main() {
    IONetworkController driver; IONetworkInterface self{&driver}; IOService maker;
    IONetworkPowerChangeNotice notice{&maker,0x123456789UL,17,kPhasePowerStateWillChange};
    IONetworkInterface::powerChangeHandler(&self,&notice,nullptr);
    require(driver.calls==1 && driver.client==&self && self.will==1 && self.did==0);
    require(self.flags==notice.powerFlags && self.state==17 && self.maker==&maker);
    notice.phase=kPhasePowerStateDidChange;
    IONetworkInterface::powerChangeHandler(&self,&notice,nullptr);
    require(driver.calls==2 && self.did==1 && self.will==1);
    notice.phase=99;
    IONetworkInterface::powerChangeHandler(&self,&notice,reinterpret_cast<void *>(1));
    require(driver.calls==2 && self.did==1 && self.will==1);
    self._driver=nullptr;
    IONetworkInterface::powerChangeHandler(&self,nullptr,nullptr);
    require(driver.calls==2 && self.did==1 && self.will==1);
}
'''
        self.check_code(code)

    def test_volatile_increment_values_and_lock_positions(self):
        source = text('IOOutputQueue.cpp')
        operations = re.findall(r'_serviceCount = _serviceCount \+ 1;', source)
        self.assertEqual(len(operations), 2)
        for signature in ('bool IOBasicOutputQueue::start(', 'bool IOBasicOutputQueue::service('):
            method = function('IOOutputQueue.cpp', signature)
            self.assertLess(method.index('QUEUE_LOCK;'), method.index(operations[0]))
            self.assertLess(method.index(operations[0]), method.index('QUEUE_UNLOCK;'))
        code = BASE + 'int main() { volatile UInt32 _serviceCount=0;\n'
        code += '\n'.join(operations) + '\nrequire(_serviceCount==2); _serviceCount=0xffffffff;\n'
        code += operations[0] + '\nrequire(_serviceCount==0); }\n'
        self.check_code(code)
        original = code.replace(operations[0], '_serviceCount++;')
        result = compile_code(original, target=FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('deprecated-volatile', result.stderr)

    def test_selected_medium_property_failure_lock_cleanup(self):
        method = function('IONetworkController.cpp', 'bool IONetworkController::setSelectedMedium(')
        code = BASE + '''struct OSSymbol {};
static OSSymbol nullName; static const OSSymbol *gIONullMediumName=&nullName;
static int gIOCurrentMediumKey=0;
struct IONetworkMedium { const OSSymbol *name; const OSSymbol *getName() const { return name; } };
#define MEDIUM_LOCK ++locks
#define MEDIUM_UNLOCK ++unlocks
struct IONetworkController {
    const OSSymbol *_lastCurrentMediumName=gIONullMediumName;
    int locks=0,unlocks=0,properties=0; bool propertyOK=true;
    bool setProperty(int,OSSymbol *) { ++properties; return propertyOK; }
    bool setSelectedMedium(const IONetworkMedium *);
};
''' + method + '''
int main() {
    IONetworkController self; OSSymbol name; IONetworkMedium medium{&name};
    require(self.setSelectedMedium(nullptr) && self.properties==0 && self.locks==self.unlocks);
    self.propertyOK=false;
    require(!self.setSelectedMedium(&medium) && self._lastCurrentMediumName==gIONullMediumName && self.properties==1);
    require(self.locks==self.unlocks);
    self.propertyOK=true;
    require(self.setSelectedMedium(&medium) && self._lastCurrentMediumName==&name && self.properties==2);
    require(self.setSelectedMedium(&medium) && self.properties==2 && self.locks==self.unlocks);
    require(self.setSelectedMedium(nullptr) && self._lastCurrentMediumName==gIONullMediumName && self.locks==self.unlocks);
}
'''
        self.check_code(code)
        original = code.replace('bool  success = true;', 'bool  success = true; bool changed=false;')
        original = original.replace('_lastCurrentMediumName = name;', 'changed=true; _lastCurrentMediumName = name;')
        result = compile_code(original, target=FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('unused-but-set-variable', result.stderr)

    def event_code(self, flexible):
        if XNU_SOURCE is not None:
            original = subprocess.check_output(['git', '-C', str(XNU_SOURCE), 'show',
                       'd8b80295118ef25ac3a784134bcf95cd8e88109f:bsd/sys/kern_event.h'], text=True)
            declaration = re.search(r'struct kern_event_msg \{.*?\n\};', original, re.S).group(0)
        else:
            declaration = '''struct kern_event_msg {
    u_int32_t total_size, vendor_code, kev_class, kev_subclass, id, event_code;
    u_int32_t event_data[XNU_KERN_EVENT_DATA_SIZE];
};'''
        size = '' if flexible else '1'
        code = BASE + '#define XNU_KERN_EVENT_DATA_SIZE ' + size + '\n'
        code += 'using u_int32_t=uint32_t; using UInt64=__UINT64_TYPE__; using size_t=__SIZE_TYPE__;\n'
        code += declaration + '''
#define KEV_MSG_HEADER_SIZE __builtin_offsetof(kern_event_msg,event_data[0])
#define offsetof(type,member) __builtin_offsetof(type,member)
#define bzero(p,n) __builtin_memset(p,0,n)
#define bcopy(s,d,n) __builtin_memcpy(d,s,n)
enum { IFNAMSIZ=16, KEV_VENDOR_APPLE=1, KEV_NETWORK_CLASS=2, KEV_DL_SUBCLASS=3,
       KEV_DL_LINK_ON=4, KEV_DL_LINK_OFF=5, TRUE=1,
       kIONetworkEventTypeLinkUp=1, kIONetworkEventTypeLinkDown=2,
       kIONetworkEventTypeLinkSpeedChange=3, kIONetworkEventTypeDLIL=4,
       kIONetworkEventWakeOnLANSupportChanged=5, kConfigDataRates=1 };
using ifnet_t=void *; using thread_call_param_t=uintptr_t;
struct IONetworkLinkEventData { UInt64 linkSpeed; };
struct if_bandwidths_t { UInt64 max_bw,eff_bw; };
static int events=0,bws=0,threads=0,logs=0; static bool pending=false;
static void *lastInterface=nullptr; static void *lastEvent=nullptr;
static thread_call_param_t lastThreadType=0; static UInt64 bandwidth=0;
alignas(uint32_t) static unsigned char message[64]; static UInt32 messageSize=0;
static UInt32 ifnet_family(ifnet_t) { return 0x12345678; }
static UInt32 ifnet_unit(ifnet_t) { return 3; }
static const char *ifnet_name(ifnet_t) { return "en-test"; }
static char *copyName(char *dest,const char *src,size_t n) {
    size_t i=0; for(; i<n && src[i]; ++i) dest[i]=src[i];
    for(;i<n;++i) dest[i]=0; return dest;
}
#define strncpy copyName
static void ifnet_set_bandwidths(ifnet_t,if_bandwidths_t *a,if_bandwidths_t *b) {
    require(a->max_bw==a->eff_bw && b->max_bw==a->max_bw); ++bws; bandwidth=a->max_bw;
}
static void ifnet_event(ifnet_t iface,kern_event_msg *event) {
    require(reinterpret_cast<uintptr_t>(event)%alignof(kern_event_msg)==0);
    require(event->total_size<=sizeof(message)); ++events; lastInterface=iface; lastEvent=event;
    messageSize=event->total_size; __builtin_memcpy(message,event,messageSize);
}
static int thread_call_enter1(void *,thread_call_param_t type) {
    ++threads; lastThreadType=type; return pending;
}
static void IOLog(const char *,...) { ++logs; }
struct IONetworkInterface {
    ifnet_t visible=reinterpret_cast<void *>(0x1000),_backingIfnet=reinterpret_cast<void *>(0x2000);
    UInt32 _configFlags=0; void *_inputEventThreadCall=nullptr; int retains=0,releases=0;
    ifnet_t getIfnet() { return visible; }
    void retain() { ++retains; } void release() { ++releases; }
    bool inputEvent(UInt32,void *);
};
''' + function('IONetworkInterface.cpp', 'bool IONetworkInterface::inputEvent(')
        code += '''
static void checkMessage(UInt32 eventCode) {
    unsigned char expected[48]={};
    const UInt32 words[8]={48,KEV_VENDOR_APPLE,KEV_NETWORK_CLASS,KEV_DL_SUBCLASS,
                           0,eventCode,0x12345678,3};
    __builtin_memcpy(expected,words,sizeof(words));
    copyName(reinterpret_cast<char *>(expected+32),"en-test",IFNAMSIZ);
    require(messageSize==sizeof(expected) && __builtin_memcmp(message,expected,sizeof(expected))==0);
}
int main() {
    static_assert(KEV_MSG_HEADER_SIZE==24,"XNU event prefix");
    IONetworkInterface self; IONetworkLinkEventData link{0x123456789ULL};
    require(self.inputEvent(kIONetworkEventTypeLinkUp,&link)); checkMessage(KEV_DL_LINK_ON);
    require(events==1 && bws==1 && bandwidth==link.linkSpeed && threads==1);
    require(self.retains==1 && self.releases==0 && lastThreadType==kIONetworkEventTypeLinkUp);
    self._configFlags=kConfigDataRates; pending=true;
    require(self.inputEvent(kIONetworkEventTypeLinkDown,&link)); checkMessage(KEV_DL_LINK_OFF);
    require(events==2 && bws==1 && threads==2 && self.retains==2 && self.releases==1);
    self._configFlags=0;
    require(self.inputEvent(kIONetworkEventTypeLinkSpeedChange,&link));
    require(events==2 && bws==2 && threads==2);
    self.visible=nullptr;
    require(self.inputEvent(kIONetworkEventTypeLinkUp,&link));
    self.visible=reinterpret_cast<void *>(0x1000);
    require(self.inputEvent(kIONetworkEventTypeLinkUp,nullptr));
    require(events==2 && bws==2 && threads==2);
    kern_event_msg raw{}; raw.total_size=sizeof(raw);
    require(self.inputEvent(kIONetworkEventTypeDLIL,&raw));
    require(events==3 && lastInterface==self._backingIfnet && lastEvent==&raw);
    require(self.inputEvent(kIONetworkEventWakeOnLANSupportChanged,nullptr));
    require(!self.inputEvent(0xabcdef,nullptr) && logs==1 && events==3);
}
'''
        return code

    def test_event_wire_bytes_both_header_modes(self):
        for flexible in (False, True):
            with self.subTest(flexible=flexible):
                self.check_code(self.event_code(flexible))

    def test_old_flexible_header_embedding_reproduces_error(self):
        code = BASE + '''struct kern_event_msg { UInt32 words[6]; UInt32 event_data[]; };
struct BadEvent { kern_event_msg header; UInt32 unit; char name[16]; };
'''
        result = compile_code(code, target=FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('gnu-variable-sized-type-not-at-end', result.stderr)

    def test_old_callback_casts_reproduce_type_errors(self):
        code = BASE + '''struct OSObject {}; struct IONetworkData {}; struct Queue {};
using Stats=IOReturn (*)(void *,void *,IONetworkData *,UInt32,void *,UInt32 *,UInt32);
using Power=IOReturn (*)(void *,void *,void *,void *,void *);
using Gate=IOReturn (*)(OSObject *,void *,void *,void *,void *);
IOReturn stats(void *,void *,IONetworkData *,UInt32);
void power(void *,void *,void *);
void gated(OSObject *,Queue *,void *,UInt32 *);
Stats a=(Stats)&stats; Power b=(Power)&power; Gate c=(Gate)&gated;
'''
        result = compile_code(code, target=FLAGS)
        self.assertNotEqual(result.returncode, 0)
        self.assertGreaterEqual(result.stderr.count('cast-function-type'), 3)

if __name__ == '__main__':
    parser=argparse.ArgumentParser(add_help=False)
    parser.add_argument('--xnu-source',type=Path)
    options,remaining=parser.parse_known_args()
    XNU_SOURCE=options.xnu_source
    unittest.main(argv=[sys.argv[0],*remaining])
