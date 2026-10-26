# SPDX-License-Identifier: BSD-2-Clause-Patent
"""Run the actual copied Stack initWithTask with typed, refcounted KPI mocks.
Runtime IOServiceOpen on the device remains an independent verification gate.
"""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1]

STUB = r'''
#pragma once
#include <cassert>
#include <cstring>
#include <string>
#include <vector>
class OSObject {
public:
    unsigned refs=1;
    virtual ~OSObject()=default;
    void retain(){++refs;}
    void release(){assert(refs>1);--refs;}
};
class OSString:public OSObject {
public:
    std::string value;
    explicit OSString(const char *p):value(p){}
    bool isEqualTo(const char *s){return value==s;}
};
class OSArray:public OSObject {
public:
    std::vector<OSObject *> values;
    unsigned getCount(){return values.size();}
    OSObject *getObject(unsigned i){return values.at(i);}
};
class OSDictionary:public OSObject {};
class OSBoolean:public OSObject {};
static OSBoolean trueValue,falseValue;
static OSObject *kOSBooleanTrue=&trueValue;
#define OSDynamicCast(t,p) dynamic_cast<t *>(p)
#define DLOG(...) ((void)0)
static const int kIOClientPrivilegeAdministrator=1,kIOReturnSuccess=0;
static const char *kIONetworkStackUserClientEntitlement="com.apple.networking.ionetworkstack.user-client";
struct Task {OSObject *modern=nullptr,*classes=nullptr;};
using task_t=Task *;
using UInt32=unsigned;
static bool baseAllowed=true;
static unsigned lookups=0,classLookups=0;
class IOUserClient {
public:
    bool initWithTask(task_t,void *,UInt32,OSDictionary *){return baseAllowed;}
    static int clientHasPrivilege(void *security,unsigned privilege){
        assert(privilege==kIOClientPrivilegeAdministrator);
        return security?0:1;
    }
    static OSObject *copyClientEntitlement(task_t task,const char *name){
        assert(task);++lookups;
        OSObject *result;
        if(!strcmp(name,kIONetworkStackUserClientEntitlement))result=task->modern;
        else {assert(!strcmp(name,"com.apple.security.iokit-user-client-class"));++classLookups;result=task->classes;}
        if(result)result->retain();
        return result;
    }
};
class IONetworkStackUserClient:public IOUserClient {
public:bool initWithTask(task_t,void *,UInt32,OSDictionary *);
};
#define super IOUserClient
'''

CASE = r'''
int main(){
    IONetworkStackUserClient client;
    OSString exact("IONetworkStackUserClient"),prefix("IONetworkStackUserClientExtra"),
             other("IO80211APIUserClient"),wrongcase("ionetworkstackuserclient");
    OSArray granted,empty,others,prefixed,casewrong,malformed;
    granted.values={&other,&exact};others.values={&other};prefixed.values={&prefix};
    casewrong.values={&wrongcase};malformed.values={&exact,&falseValue};
    Task task;void *admin=(void *)1;
    auto check=[&](bool expected){
        assert(client.initWithTask(&task,admin,0,nullptr)==expected);
        for(OSObject *p:{(OSObject *)&trueValue,(OSObject *)&falseValue,
                        (OSObject *)&granted,(OSObject *)&empty,(OSObject *)&others,
                        (OSObject *)&prefixed,(OSObject *)&casewrong,(OSObject *)&malformed,
                        (OSObject *)&exact})assert(p->refs==1);
    };
    // Root without any signed permission is refused.
    check(false);
    task.classes=&granted;check(BOARD);
    // No substring, wrong-case, bare string, wrong type or malformed policy.
    for(OSObject *p:{(OSObject *)&empty,(OSObject *)&others,(OSObject *)&prefixed,
                    (OSObject *)&casewrong,(OSObject *)&exact,(OSObject *)&trueValue,
                    (OSObject *)&malformed}){task.classes=p;check(false);}
    task.classes=&granted;
    // Owning task's policy is used, not the caller's class or claimed name.
    Task foreign;assert(!client.initWithTask(&foreign,admin,0,nullptr));
    task.modern=&trueValue;check(true);
    unsigned before=classLookups;
    task.modern=&falseValue;check(false);assert(classLookups==before);
    task.modern=&exact;check(false);assert(classLookups==before);
    // Entitlements do not bypass actual caller administrator/super gates.
    task.modern=&trueValue;before=lookups;admin=nullptr;check(false);assert(lookups==before);
    admin=(void *)1;task.modern=nullptr;before=lookups;baseAllowed=false;check(false);assert(lookups==before);
    baseAllowed=true;
    if(!BOARD)assert(classLookups==0);
}
'''


class StackAuthorization(unittest.TestCase):
    def test_actual_stack_policy_and_reference_ownership(self):
        text=(SOURCE/'apple/IONetworkStack.cpp').read_text()
        start=text.index('bool IONetworkStackUserClient::initWithTask(')
        opening=text.index('{',start)
        depth=0
        for end in range(opening,len(text)):
            if text[end]=='{':depth+=1
            elif text[end]=='}':
                depth-=1
                if not depth:break
        actual=text[start:end+1]
        with tempfile.TemporaryDirectory() as directory:
            d=Path(directory)
            (d/'libkern/c++').mkdir(parents=True)
            for name in ('OSArray.h','OSString.h'):
                (d/'libkern/c++'/name).write_text('#include "stub.h"\n')
            (d/'stub.h').write_text(STUB)
            (d/'test.cpp').write_text('#include "stub.h"\n#if BOARD\n#include "A04BR3StackAuthorization.h"\n#endif\n'+actual+'\n'+CASE)
            for board in (0,1):
                binary=d/('test'+str(board))
                flags=['-DBOARD='+str(board)]
                if board:flags+=['-DARM64_BOARD_CONFIG_A04BR3=1']
                subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',
                    '-fsanitize=address,undefined',*flags,'-I',str(d),'-I',str(SOURCE/'apple'),
                    str(d/'test.cpp'),'-o',str(binary)],check=True)
                subprocess.run([str(binary)],check=True)


if __name__=='__main__':unittest.main()
