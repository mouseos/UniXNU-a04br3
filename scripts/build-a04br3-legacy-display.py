#!/usr/bin/env python3
"""Build the a04br3 legacy display archive: the iPadOS display on the firmware frame buffer (CONFIG_XNU_NATIVE_LEGACY_DISPLAY).

From github.com/xVanTuring/UniXNU-tb128fu fdb4434 scripts/build-native-legacy-display.py (itself UniXNU-qemu's, reduced from
UniXNU-sheng's). The checks of the original IOMobileFramebufferLegacy class and its bindings are copied unchanged from there
(the block from "audit=" to the late object). a04br3 differs in its inputs and in what the archive carries:

  - The reference candidate is this board's previous prelinked kernel (out/a04br3/kernel/kernel.macho and its .json), staged as
    build/native-prelink/native-owner-ufs-cpu-desktop-a04br3-candidate.macho. UniXNU scripts/audit-mobile-graphics-legacy.py and
    scripts/build-native-hid-input.py take such a candidate when theirs are pruned: it is built from the same original
    kernelcache, so it carries the same original IOMobileGraphicsFamily and IOHIDFamily, and both scripts check their
    instruction hashes. Nothing of UniXNU-qemu's tree is read, so a new tree (the macOS workflow) builds it.
  - The QEMU observation of the original class (gdb-stop.json, identity IOMobileFramebufferLegacy, parent 0xfffffff006b31400,
    object size 0x18a0) is not in a new tree. The audit then carries over the hash it records for that observation (RUNTIME_SHA
    of audit-mobile-graphics-legacy.py), but only from an earlier result in build/display-analysis; this script writes that
    record, with the hash read from the audit script itself, before it runs the audit. Every static check of the audit runs.
  - The archive holds NativeLegacyDisplay, NativeGOPPresenter, LegacyDisplayProbe.cpp, HidInputProbe.cpp, TouchState.cpp and
    NativeHidInput (with the original-class bindings). tb128fu's touch controller, Adreno and RPM probes and their firmware
    blobs are left out (other hardware). KeyStoreProbe.cpp and NativeKeyStoreEvents are left out as well: they are already in
    the board archive (scripts/build-a04br3-board.py, started from unixnu_board_init()), and the kernel links both archives.
  - Without a previous kernel (the first kernel pass of a new tree, scripts/a04br3-pre-build.sh), or with one prelinked without
    IOMobileGraphicsFamily, it archives no-op native_legacy_display_probe/native_legacy_display_late_probe, the two symbols the
    kernel calls (UniXNU patches/xnu/offline-component-load-probes.patch, offline-legacy-start.patch), as
    build-platform-power.py does for the PMGR bridge. The next kernel build then links the display.

The kernel step (UniXNU scripts/build-xnu-4k-baseline.sh, NATIVE_LEGACY_DISPLAY) force-loads
build/native-legacy-display/libNativeLegacyDisplay.late.a and checks the archive and source hashes of build-report.json.
After the archive is made, its definitions are compared with the board archive (duplicates stop the build) and its
undefined symbols with the previous kernel, the board archive and itself (unresolved names are reported).
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'build/native-legacy-display';OUT.mkdir(parents=True,exist_ok=True)
sys.path.insert(0,str(ROOT/'kits/NativePrelink'))
from native_prelink import macho,backed_offset,exports
def run(cmd,**kw):return subprocess.check_output([str(x) for x in cmd],timeout=90,**kw)
def require(v,why):
    if not v:raise SystemExit(why)
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
FAMILY='com.apple.iokit.IOMobileGraphicsFamily'
KIT=ROOT/'kits/a04br3'
archive=OUT/'libNativeLegacyDisplay.late.a'
board_kernel=ROOT.parent/'kernel/kernel.macho'

def first_pass_stub(reason):
    stub=OUT/'FirstPassStub.c'
    stub.write_text('// No reference kernel with the original display family yet (build-a04br3-legacy-display.py): the display needs one,\n'
                    '// so this kernel links without it. Build the kernel again to link the display.\n'
                    'void native_legacy_display_probe(void);\nvoid native_legacy_display_probe(void)\n{\n}\n'
                    'void native_legacy_display_late_probe(void);\nvoid native_legacy_display_late_probe(void)\n{\n}\n'
                    '#include <stddef.h>\n'
                    '// The first kernel has no HID/display implementation. XNU does not permit unresolved weak imports.\n'
                    'unsigned native_hid_input_command;\n'
                    'void *native_gop_handoff_memory(void) { return 0; }\n'
                    'int native_gop_handoff_acquire(void *owner) { (void)owner; return (int)0xe00002c7U; }\n'
                    'void native_gop_handoff_close(void *owner) { (void)owner; }\n'
                    'int native_gop_handoff_call(unsigned op, void *task, const void *in, size_t n, void *out, size_t m)\n'
                    '{ (void)op; (void)task; (void)in; (void)n; (void)out; (void)m; return (int)0xe00002c7U; }\n'
                    'int native_gop_handoff_call_owned(void *owner, unsigned op, void *task, const void *in, size_t n, void *out, size_t m)\n'
                    '{ (void)owner; return native_gop_handoff_call(op, task, in, n, out, m); }\n')
    run(['xcrun','--sdk','iphoneos','clang','-arch','arm64','-mkernel','-miphoneos-version-min=17.0','-Os','-c',stub,'-o',OUT/'FirstPassStub.o'])
    archive.unlink(missing_ok=True)
    run(['xcrun','ar','rcs',archive,OUT/'FirstPassStub.o'])
    (OUT/'build-report.json').write_text(json.dumps(dict(first_pass_stub=True,reason=reason,archive_sha256=sha(archive),
        source_sha256={str(stub.relative_to(ROOT)):sha(stub)},display_presented=False),indent=2)+'\n')
    print(f'Legacy display: {reason}; linked no-op display probes. Build the kernel again to link the display.')
    raise SystemExit(0)

if not board_kernel.is_file() or not board_kernel.with_suffix('.json').is_file():
    first_pass_stub(f'no previous kernel at {board_kernel}')
board_plan=json.loads(board_kernel.with_suffix('.json').read_text())
if not any(e['module']==FAMILY for e in board_plan.get('extents',[])):
    first_pass_stub(f'the previous kernel was prelinked without {FAMILY} ([kernel.prelink] roots)')

# The reference candidate: this board's previous kernel, under the name the audit and the HID builder look for.
reference=ROOT/'build/native-prelink/native-owner-ufs-cpu-desktop-a04br3-candidate.macho'
reference.parent.mkdir(parents=True,exist_ok=True)
shutil.copyfile(board_kernel,reference);shutil.copyfile(board_kernel.with_suffix('.json'),reference.with_suffix('.json'))
# The recorded hash of the QEMU observation, for the audit's path without gdb-stop.json.
recorded=re.search(r"RUNTIME_SHA='([0-9a-f]{64})'",(ROOT/'scripts/audit-mobile-graphics-legacy.py').read_text())
require(recorded,'audit-mobile-graphics-legacy.py no longer records RUNTIME_SHA')
observation=ROOT/'build/darwinboot-owner-hvf-mobile-graphics-legacy-class/gdb-stop.json'
analysis=ROOT/'build/display-analysis/mobile-graphics-legacy-abi.json'
analysis.parent.mkdir(parents=True,exist_ok=True)   # the audit writes its result there without creating it
if not observation.exists():
    try:previous=json.loads(analysis.read_text()).get('runtime_metadata_checkpoint_sha256')
    except (OSError,ValueError):previous=None
    if previous!=recorded.group(1):
        analysis.write_text(json.dumps(dict(runtime_metadata_checkpoint_sha256=recorded.group(1),
            note='Recorded by build-a04br3-legacy-display.py from audit-mobile-graphics-legacy.py RUNTIME_SHA; the audit replaces this file'),indent=2)+'\n')
print(run(['python3',ROOT/'scripts/audit-mobile-graphics-legacy.py'],text=True),end='')
audit=json.loads((ROOT/'build/display-analysis/mobile-graphics-legacy-abi.json').read_text())
pure=audit['abstract_slots'];methods=audit['methods']
header='''// SPDX-License-Identifier: BSD-2-Clause-Patent
// Opaque ABI storage for the exact original class, not source-level access
// to its private fields. The actual original metadata supplies its full
// Framebuffer/Service/IOService ancestry. Only qualified base prototypes may
// be called from source; opaque entries are original dispatch targets only.
#pragma once
#include <IOKit/IOService.h>
#include <libkern/libkern.h>
struct OriginalLegacySwapRequest;
class IOMobileFramebufferLegacy : public IOService {
 OSDeclareAbstractStructors(IOMobileFramebufferLegacy)
 unsigned char originalPrivate[0x18a0-sizeof(IOService)];
public:
 // Both fields are qualified by the complete original slot-249 getter.
 // This modifies this object's own mode state, never a vtable or executable.
 void installBootDimensions(uint32_t width,uint32_t height) {
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5808)=width;
  *reinterpret_cast<uint32_t *>(reinterpret_cast<unsigned char *>(this)+5812)=height;
 }
 const OriginalLegacySwapRequest *pendingSwapRequest() const {
  const OriginalLegacySwapRequest *request=nullptr;
  memcpy(&request,reinterpret_cast<const unsigned char *>(this)+5480,sizeof(request));return request;
 }
 // The real normal scanout backend removes its pending head before calling
 // slot 295. Slot 312 is a discard path (terminal state 8), not presentation.
 // The queue is a tail queue (head at +5480, tail pointer at +5488, each request's next at +2960 and previous-link at +2968): taking the head
 // moves the next request, if a client already queued one behind it (int-132: swap 3 arrived during swap 2's GPU copy and the old
 // single-entry form refused to retire swap 2, so every wait on it stayed busy), up to the head. The original queue's reference
 // moves to the current-swap slot for the original completion body.
 bool activateOnlyPendingSwap(const OriginalLegacySwapRequest *request) {
  auto self=reinterpret_cast<unsigned char *>(this);
  auto bytes=reinterpret_cast<const unsigned char *>(request);
  const void *current=nullptr,*head=nullptr,*tail=nullptr,*next=nullptr,*previous=nullptr;
  memcpy(&current,self+5472,8);memcpy(&head,self+5480,8);memcpy(&tail,self+5488,8);
  if(!request || current || head!=request)return false;
  memcpy(&next,bytes+2960,8);memcpy(&previous,bytes+2968,8);
  if(previous!=self+5480)return false;
  if(next) {
   const void *link=self+5480;
   memcpy(const_cast<unsigned char *>(reinterpret_cast<const unsigned char *>(next))+2968,&link,8);
  } else if(tail!=bytes+2960)return false;
  memcpy(self+5472,&request,8);
  if(next) {memcpy(self+5480,&next,8);}
  else {const void *empty=nullptr,*emptyTail=self+5480;memcpy(self+5480,&empty,8);memcpy(self+5488,&emptyTail,8);}
  return true;
 }
 void free() override;
 bool init(OSDictionary *properties=nullptr) override;
 IOReturn setProperties(OSObject *) override;
 void systemWillShutdown(IOOptionBits) override;
 IOService *probe(IOService *,SInt32 *) override;
 bool start(IOService *) override;
 void stop(IOService *) override;
 IOWorkLoop *getWorkLoop() const override;
 IOReturn newUserClient(task_t,void *,UInt32,IOUserClient **) override;
 IOReturn setPowerState(unsigned long,IOService *) override;
'''
for i in range(168,358):
    if i==184:header+=' virtual bool isMainDisplay();\n'
    elif i==188:header+=' virtual IOReturn getDotPitch(uint32_t *);\n'
    elif i==213:header+=' virtual IOReturn supportedFrameInfo(uint32_t,uint32_t *,char *,uint32_t *)=0;\n'
    elif i==249:header+=' virtual void getDisplayDimensions(uint32_t *,uint32_t *);\n'
    elif i==290:header+=' virtual void kickSwapQueue();\n'
    elif i==296:header+=' virtual bool validateSwap(const OriginalLegacySwapRequest *);\n'
    elif i==301:header+=' virtual bool getRefreshAttributes(uint64_t *,bool);\n'
    elif i==295:header+=' virtual void completeCurrentSwap();\n'
    elif i==337:header+=' virtual IOReturn collectCompletionReports(uint32_t,void *,uint32_t *,uint32_t)=0;\n'
    elif i==288:header+=' virtual bool startHardware();\n'
    elif i in pure:header+=f' virtual __attribute__((noreturn)) void opaque{i}()=0;\n'
    else:header+=f' virtual __attribute__((deprecated("Unclassified ABI: never call from source"))) void opaque{i}();\n'
header+='};\nstatic_assert(sizeof(IOMobileFramebufferLegacy)==0x18a0,"Actual original instance extent");\n'
(OUT/'OriginalLegacyFramebufferABI.h').write_text(header)
print(run(['python3',ROOT/'scripts/build-arm-driver.py','NativeLegacyDisplay'],text=True),end='')
obj=OUT/'NativeLegacyDisplay.o';raw=obj.read_bytes();image=macho(raw)
st=next(p for c,p,_ in image['commands'] if c==2);sy,count,strings,_=struct.unpack_from('<4I',raw,st+8);symbols=[]
for i in range(count):
    ix,kind,section,_,va=struct.unpack_from('<IBBHQ',raw,sy+i*16)
    symbols.append((raw[strings+ix:raw.index(b'\0',strings+ix)].decode(),va,kind))
vtable=next(va for name,va,_ in symbols if name=='__ZTV19NativeLegacyDisplay');relocations={}
for segment in image['segments']:
    for sec in segment['sections']:
        if sec['va']<=vtable<sec['va']+sec['size']:
            ro,rc=struct.unpack_from('<2I',raw,sec['command_offset']+56)
            for i in range(rc):
                offset,bits=struct.unpack_from('<2I',raw,ro+i*8)
                if bits>>27&1 and bits>>28==0 and bits>>25&3==3:relocations[sec['va']+offset]=symbols[bits&0xffffff][0]
compiled=[relocations.get(vtable+16+i*8) for i in range(358)]
require(all(compiled) and vtable+16+358*8 not in relocations,'Exact owned 358-entry vtable')
candidate=ROOT/'build/native-prelink/native-owner-mobile-graphics-candidate.macho'
if not candidate.exists():  # pruned: same fallback as scripts/audit-mobile-graphics-legacy.py (identical original family executable)
    later=sorted((ROOT/'build/native-prelink').glob('native-owner-ufs-cpu-desktop-*-candidate.macho'),key=lambda f:f.stat().st_mtime)
    require(later,'No reference candidate available');candidate=later[-1]
old=candidate.read_bytes();old_image=macho(old)
names={n:v for v,group in exports(old,old_image).items() for n in group}
def read(va,n):
    at=backed_offset(old_image['segments'],va,n,len(old));return old[at:at+n]
bindings={'__ZN25IOMobileFramebufferLegacyC2EPK11OSMetaClass':0xfffffff0057865cc,
          '__ZN25IOMobileFramebufferLegacyD2Ev':0xfffffff005786618,
          '__ZN25IOMobileFramebufferLegacy10gMetaClassE':0xfffffff006b35940,
          '__ZTV25IOMobileFramebufferLegacy':int(audit['legacy_vptr'],16)-16}
owned=[];native=[]
for i,name in enumerate(compiled):
    expected=int(methods[i]['address'],16)
    if name.startswith(('__ZN19NativeLegacyDisplay','__ZNK19NativeLegacyDisplay')):
        owned.append(i);require(i in (0,1,7,14,84,86,87,141,155,184,188,288,290,296,301) or i in pure,'Only qualified own methods and guarded leaves override')
    elif '25IOMobileFramebufferLegacy' in name:
        require(i not in pure,'No unresolved original abstract backend target');bindings[name]=expected
    else:
        require(i<168 and names.get(name)==expected,'Actual inherited native base symbol '+str(i));native.append(i)
require(owned==sorted([0,1,7,14,84,86,87,141,155,184,188,288,290,296,301]+pure),'Exact owned method set')
undefined={n for n,_,k in symbols if k==1 and 'IOMobileFramebufferLegacy' in n}
require(undefined==set(bindings),'All and only actual original-class imports qualified')
# Exact original IOSurface getters, independently observed through the real
# original swap request. These aliases call original code; no object layout
# is substituted and no original method/vtable is patched.
for name,address,field,wide in [
    ('descriptor',0xfffffff004b9ead0,48,True),('width',0xfffffff004b9ebbc,88,True),
    ('height',0xfffffff004b9ebc4,96,True),('stride',0xfffffff004b9ebd4,104,True),
    ('allocation',0xfffffff004b9ebfc,144,True),('format',0xfffffff004b9ebac,128,False),
    ('planes',0xfffffff004b9ebf4,152,False)]:
    expected=(0xf9400000 if wide else 0xb9400000)|((field//(8 if wide else 4))<<10)
    require(read(address,8)==struct.pack('<2I',expected,0xd65f03c0),'Exact original surface getter '+name)
    bindings['_native_legacy_surface_'+name]=address
# The panel refresh rate of NativeMobileDisplay c031d88 (sheng-vsync=<Hz>), as UniXNU-sheng's copy of this script qualifies it; the
# target kernel cache is the same. The original display-refresh notification sender (self, timestamp, continuous host time, frame
# interval): it sends the six-word vsync message (type 5) to every client entry queued at +5592, the tail queue that selector 72's
# registration (slot 208) fills at +5512 + type*16. The platform subclass that normally calls it on each panel vblank is not part of
# this port.
require(read(0xfffffff005796720,16)==bytes.fromhex('ff0302d1fd7b07a9fdc30191a0831ff8') and
        read(0xfffffff005796830,4)==bytes.fromhex('08ed4af9') and
        read(0xfffffff00579685c,16)==bytes.fromhex('e2230091c3008052240080521684ff97') and
        read(0xfffffff005795fbc,4)==bytes.fromhex('0ab182d2'),
        'Original vsync sender, its +5592 queue and six-word drop-allowed async send')
bindings['_native_legacy_vsync_notify']=0xfffffff005796720
# Slot 301 fills the six refresh words that setDisplayRefreshProperties publishes as IOMFBDisplayRefresh (minimum interval, its mach
# time, maximum interval, its mach time, step, its mach time; intervals in 32.32 seconds): the generic body fixes the minimum at
# 0x04444444 (1/60 s), and the render server paces its display timer by it.
require(int(methods[301]['address'],16)==0xfffffff00579ea5c and read(0xfffffff00579ea68,8)==bytes.fromhex('888888d28888a0f2') and
        read(0xfffffff00579ec54,4)==bytes.fromhex('08b544f9'),
        'Original slot-301 refresh words with the 1/60 s minimum, called by setDisplayRefreshProperties')
# setDisplayRefreshProperties itself (self): fills the six words through slot 301 and publishes them as IOMFBDisplayRefresh. Nothing in
# the family calls it (the platform subclass does), so without it the render server falls back to its own 1/60 s heartbeat.
require(read(0xfffffff00579ebb8,16)==bytes.fromhex('ff0302d1fd7b07a9fdc30191a0031ff8') and
        read(0xfffffff00579ed54,16)==bytes.fromhex('086540f90173ff90218c0c9100013fd6'),
        'Original setDisplayRefreshProperties publishing IOMFBDisplayRefresh through setProperty')
bindings['_native_legacy_set_refresh_properties']=0xfffffff00579ebb8
# Both protected destructors/constructors must follow the exact original chain.
def branch(pc):
    word=struct.unpack('<I',read(pc,4))[0];require(word&0xfc000000==0x94000000,'Actual protected base call')
    value=word&0x3ffffff;return pc+4*(value-(1<<26) if value&(1<<25) else value)
require(branch(0xfffffff0057865fc)==0xfffffff00577b510 and branch(0xfffffff005786630)==0xfffffff00577b55c,
        'Original protected Legacy C2/D2 really call their original Service bases')
# Actual caller passes only self at slot 288 and tests bit zero of w0.
require(read(0xfffffff005787ef8,16)==bytes.fromhex('080040f9088144f900013fd6e0000037'),
        'Original bool start_hardware() call and result check')
getter=int(methods[249]['address'],16)
for offset,field in [(0x30,5808),(0x58,5812)]:
    require(struct.unpack('<I',read(getter+offset,4))[0]==0xb9400000|((field//4)<<10)|(8<<5)|8,
            'Actual original width/height getter fields')
require(int(methods[295]['address'],16)==0xfffffff005795b88 and
        read(0xfffffff005795b98,16)==bytes.fromhex('e00740f9280080520101001272d3ff97'),
        'Actual original slot-295 passes true to the normal current completion body')
require(read(0xfffffff00578aa7c,24)==bytes.fromhex('080040f9084545f9030080d2e20303aa0400805200013fd6'),
        'Original completion calls optional report slot 337 with null outputs and zero limit')
require(read(0xfffffff005777e9c,16)==bytes.fromhex('080040f908e142f900013fd6c0000036'),
        'Original isMainDisplay dispatch uses only self and tests boolean bit zero')
asm=OUT/'original-bindings.S';asm.write_text(''.join(f'.globl {name}\n.set {name}, {va:#x}\n' for name,va in sorted(bindings.items())))
sdk=run(['xcrun','--sdk','iphoneos','--show-sdk-path'],text=True).strip()
run(['xcrun','clang','-target','arm64-apple-ios17.0','-isysroot',sdk,'-c',asm,'-o',OUT/'original-bindings.o'])
llvm=Path(run(['brew','--prefix','llvm'],text=True).strip())/'bin';late=OUT/'NativeLegacyDisplay.late.o'
run([llvm/'llvm-objcopy','--remove-section=__DATA,__mod_init_func','--globalize-symbol=__GLOBAL__sub_I_NativeLegacyDisplay.cpp',obj,late])
def code(data,info):
    sec=next(s for g in info['segments'] for s in g['sections'] if s['name']=='__text');return data[sec['fileoff']:sec['fileoff']+sec['size']]
require(code(raw,image)==code(late.read_bytes(),macho(late.read_bytes())),'Only owned initializer section registration changes')
# The board probes with the kernel's C++ flags (the record of IOPlatformExpert.cpp, as tb128fu builds them).
r=json.loads((ROOT/'build/xnu/ios-obj/DEVELOPMENT_ARM64_BCM2837/iokit/DEVELOPMENT/IOPlatformExpert.cpo.json').read_text().strip().rstrip(','))
cmd=[];args=r['arguments'];i=0;source=KIT/'LegacyDisplayProbe.cpp'
while i<len(args):
    value=args[i]
    if value==r['file']:cmd.append(str(source))
    elif value=='-o':cmd+=['-o',str(OUT/'LegacyDisplayProbe.o')];i+=1
    elif value=='-Xclang' and args[i+1].startswith('-W'):i+=1
    elif not value.startswith('-W'):cmd.append(value)
    i+=1
cmd+=['-Wall','-Wextra','-Werror','-Wno-unused-parameter','-Wno-unnecessary-virtual-specifier','-Wno-c99-designator','-Wno-reorder-init-list',
      '-Wno-deprecated-enum-enum-conversion','-I',str(ROOT/'.cache/xnu-work/osfmk')]
run(cmd,cwd=r['directory'])
def compile_like_probe(other_source,other_obj):
    run([str(other_source) if str(arg)==str(source) else str(other_obj) if str(arg)==str(OUT/'LegacyDisplayProbe.o') else arg for arg in cmd],
        cwd=r['directory'])
hid_source=KIT/'HidInputProbe.cpp';hid_obj=OUT/'HidInputProbe.o';compile_like_probe(hid_source,hid_obj)
touch_source=KIT/'TouchState.cpp';touch_obj=OUT/'TouchState.o';compile_like_probe(touch_source,touch_obj)
# The board's touch panel and keys (package/A04BR3Input.cpp, a04br3-input=1), from the iOS 16 bring-up of this tablet.
input_source=KIT/'A04BR3Input.cpp';input_obj=OUT/'A04BR3Input.o';compile_like_probe(input_source,input_obj)
# NativeHidInput against the original IOHIDDevice (UniXNU scripts/build-native-hid-input.py), with the staged candidate.
print(run(['python3',ROOT/'scripts/build-native-hid-input.py'],text=True),end='')
hid_dir=ROOT/'build/native-hid-input'
print(run(['python3',ROOT/'scripts/build-arm-driver.py','NativeGOPPresenter'],text=True),end='')
gop=ROOT/'build/native-gop-presenter';gop_late=gop/'NativeGOPPresenter.late.o'
run([llvm/'llvm-objcopy','--remove-section=__DATA,__mod_init_func','--globalize-symbol=__GLOBAL__sub_I_NativeGOPPresenter.cpp',
     gop/'NativeGOPPresenter.o',gop_late])
require(code((gop/'NativeGOPPresenter.o').read_bytes(),macho((gop/'NativeGOPPresenter.o').read_bytes()))==
        code(gop_late.read_bytes(),macho(gop_late.read_bytes())),'Owned presenter instructions unchanged by late init staging')
members=[late,OUT/'original-bindings.o',OUT/'LegacyDisplayProbe.o',hid_obj,touch_obj,input_obj,hid_dir/'NativeHidInput.late.o',
         hid_dir/'hid-original-bindings.o',gop_late]
archive.unlink(missing_ok=True)
run(['xcrun','ar','rcs',archive,*members])

# Static link checks. Mach-O symbol kinds from llvm-nm -m: "(undefined) [weak ]external _x" and "(<section>|absolute|common) ...".
def symbols(path):
    defined,weak,undefined,weak_imports=set(),set(),set(),set()
    for line in run([llvm/'llvm-nm','-m',path],text=True).splitlines():
        if 'non-external' in line:continue
        # New LLVM prints attributes such as [no dead strip] before the name.
        # They describe the symbol; they are not duplicate definitions named "[no".
        m=re.search(r'\(([^)]*)\) (?:\(alignment [^)]*\) )?(weak )?(?:private )?external (?:\[[^]]*\]\s+)*(\S+)',line)
        if not m:continue
        kind,is_weak,name=m.groups()
        if kind=='undefined':(weak_imports if is_weak else undefined).add(name)
        else:(weak if is_weak else defined).add(name)
    return defined,weak,undefined,weak_imports
own_defined,own_weak,own_undefined,own_weak_imports=symbols(archive)
board_archive=ROOT/'build/a04br3-board/libA04BR3Board.a'
require(board_archive.is_file(),f'Missing {board_archive}: board.toml must run build-a04br3-board.py before this script')
board_defined,board_weak,_,_=symbols(board_archive)
duplicates=sorted(own_defined&board_defined)
require(not duplicates,'Defined in both the display and the board archive: '+', '.join(duplicates))
kernel_names=set()
for line in run([llvm/'llvm-nm',reference],text=True).splitlines():
    parts=line.split()
    if len(parts)==3:kernel_names.add(parts[2])
unresolved=sorted(own_undefined-own_defined-own_weak-board_defined-board_weak-kernel_names)
for name in unresolved:print('WARNING: the display archive needs '+name+', defined neither in the previous kernel nor in the board archive')
print(f'Static link check: {len(own_defined)} definitions, none also in the board archive; {len(own_undefined)} imports, '
      f'{len(unresolved)} unresolved; weak imports {sorted(own_weak_imports)}')

sources=[ROOT/'drivers/NativeMobileDisplay/NativeLegacyDisplay.cpp',ROOT/'drivers/NativeMobileDisplay/NativeLegacyRequest.h',source,
         hid_source,touch_source,ROOT/'drivers/NativeHidInput/NativeHidInput.cpp',ROOT/'scripts/build-native-hid-input.py',
         ROOT/'drivers/NativeMobileDisplay/NativeGOPPresenter.cpp',ROOT/'drivers/NativeMobileDisplay/NativeGOPWire.h',
         ROOT/'scripts/audit-mobile-graphics-legacy.py',ROOT/'scripts/build-a04br3-legacy-display.py']
(OUT/'build-report.json').write_text(json.dumps(dict(
    source_sha256={str(p.relative_to(ROOT)):sha(p) for p in sources},generated_abi_sha256=sha(OUT/'OriginalLegacyFramebufferABI.h'),
    original_candidate_sha256=sha(candidate),original_audit_sha256=sha(analysis),
    compiled_object_sha256=sha(obj),late_object_sha256=sha(late),archive_sha256=sha(archive),archive_members=[m.name for m in members],
    bindings={n:hex(v) for n,v in bindings.items()},virtual_entries=358,owned_entries=owned,inherited_native_entries=native,
    guarded_backend_leaves=[i for i in pure if i not in (213,337)],duplicate_definitions=duplicates,unresolved_imports=unresolved,
    weak_imports=sorted(own_weak_imports),original_instructions_modified=False,driver_initialized=False,display_presented=False,desktop=False),indent=2)+'\n')
print('Built the a04br3 legacy display archive: NativeLegacyDisplay on the GOP frame buffer, NativeGOPPresenter and the HID input')
