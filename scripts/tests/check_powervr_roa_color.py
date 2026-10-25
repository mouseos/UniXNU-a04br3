#!/usr/bin/env python3
"""Validate actual ROA gate and GLSL, optionally run shipped Vulkan probe on CPU lavapipe only."""
from pathlib import Path
import argparse, runpy, subprocess, tempfile
R=Path(__file__).resolve().parents[2]
p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--lavapipe',type=Path,help='explicit CPU-only libvulkan_lvp.so for reference execution')
a=p.parse_args()
mesa=R/'.cache/mesa-pvr';user=R/'package/powervr/user'
s=(mesa/'src/imagination/vulkan/pvr_physical_device.c').read_text()
start=s.index('static bool pvr_roa_color_enabled(');end=s.index('\n}',start)+2
helper=s[start:end]
fixture=r'''
#include <stdbool.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
struct pvr_device_info{struct{unsigned b,v,n,c;}ident;};
'''+helper+r'''
int main(void){struct pvr_device_info d={{22,40,54,30}};
unsetenv("PVR_DARWIN_ROA_COLOR");assert(!pvr_roa_color_enabled(&d));
const char *values[]={"","0","2","01","1 ","true","1"};
for(unsigned i=0;i<sizeof(values)/sizeof(*values);i++){
setenv("PVR_DARWIN_ROA_COLOR",values[i],1);
assert(pvr_roa_color_enabled(&d)==(DARWIN_TEST&&i==6));}
for(unsigned i=0;i<4;i++){unsigned *fields[]={&d.ident.b,&d.ident.v,&d.ident.n,&d.ident.c};
unsigned old=*fields[i];*fields[i]=old+1;assert(!pvr_roa_color_enabled(&d));*fields[i]=old;}
}
'''
# Exercise the actual pipeline selection after NIR info is rebuilt: a writer
# without an input load must still stay translucent under COLOR ROA.
pipe=(mesa/'src/imagination/vulkan/pvr_arch_pipeline.c').read_text()
start=pipe.index('#ifdef PVR_WINSYS_DARWIN\n      /* The common state')
block=pipe[start:pipe.index('#endif',start)+6]
start=pipe.index('static void pvr_fragment_state_save(');end=pipe.index('\n}',start)+2
save=pipe[start:end]
info=(mesa/'src/imagination/pco/pco_nir.c').read_text()
assign=next(x.strip() for x in info.splitlines() if 'data->fs.uses.fbfetch = nir->info.fs.uses_fbfetch_output' in x)
fixture2=r'''
#include <assert.h>
#include <stdbool.h>
#include <string.h>
#define PVR_WINSYS_DARWIN 1
#define VK_IMAGE_ASPECT_COLOR_BIT 1
#define ROGUE_TA_PASSTYPE_OPAQUE 0
#define ROGUE_TA_PASSTYPE_TRANSLUCENT 1
#define ROGUE_TA_PASSTYPE_PUNCH_THROUGH 2
#define ROGUE_TA_PASSTYPE_DEPTH_FEEDBACK 3
typedef struct {struct{bool roa_color;struct{bool fbfetch,olchk_skip,depth_feedback,discard;}uses;}fs;}pco_data;
typedef struct {pco_data data;}pco_shader;
struct native_nir{struct{struct{bool uses_fbfetch_output;}fs;}info;};
struct native_state{unsigned rasterization_order_access;};
struct pvr_fragment_shader_state{unsigned pass_type;struct{unsigned pds_temps_count;}stage_state;};
struct pvr_graphics_pipeline{struct{struct pvr_fragment_shader_state fragment;}shader_state;pco_data fs_data;};
static const pco_data *pco_shader_data(pco_shader*s){return &s->data;}
'''+save+r'''
int main(void){for(unsigned mask=0;mask<2;mask++){
pco_shader shader={0};pco_data *data=&shader.data;struct native_nir n={0},*nir=&n;
struct native_state st={mask},*state=&st;
'''+block+r'''
// Real gather_info clears uses_fbfetch_output for a nonfetch writer.
nir->info.fs.uses_fbfetch_output=false;
'''+assign+r'''
struct pvr_graphics_pipeline pipeline={0};pvr_fragment_state_save(&pipeline,&shader);
assert(pipeline.shader_state.fragment.pass_type==(mask?ROGUE_TA_PASSTYPE_TRANSLUCENT:ROGUE_TA_PASSTYPE_OPAQUE));
assert(!data->fs.uses.olchk_skip);
data->fs.uses.discard=true;pvr_fragment_state_save(&pipeline,&shader);assert(pipeline.shader_state.fragment.pass_type==ROGUE_TA_PASSTYPE_PUNCH_THROUGH);
data->fs.uses.depth_feedback=true;pvr_fragment_state_save(&pipeline,&shader);assert(pipeline.shader_state.fragment.pass_type==ROGUE_TA_PASSTYPE_DEPTH_FEEDBACK);
}}
'''

subprocess.run(['python3',str(user/'shaders/gen_roa.py'),'--check'],check=True)
with tempfile.TemporaryDirectory(prefix='pvr-roa-') as directory:
 t=Path(directory);src=t/'gate.c';src.write_text(fixture)
 for darwin in (0,1):
  binary=t/f'gate{darwin}'
  subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',
    '-DDARWIN_TEST='+str(darwin),*(['-DPVR_WINSYS_DARWIN'] if darwin else []),str(src),'-o',str(binary)],check=True)
  subprocess.run([str(binary)],check=True)
 src.write_text(fixture2);binary=t/'writer'
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-pie','-no-pie',str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
 constructor=runpy.run_path(str(R/'scripts/build-a04br3-powervr-probe.py'))['roa_color_probe_source']()
 src.write_text(constructor+'\n#include <assert.h>\n#include <string.h>\nint main(void){assert(!strcmp(getenv("PVR_PROBE_ROA_COLOR"),"1"));assert(!strcmp(getenv("PVR_DARWIN_ROA_COLOR"),"1"));}\n')
 binary=t/'constructor'
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror',str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True)
 for flags in (['--roa-color'],['--roa-color','--vulkan','unused','--ui-fill']):
  rejected=subprocess.run(['python3',str(R/'scripts/build-a04br3-powervr-probe.py'),*flags,'--check'],capture_output=True)
  assert rejected.returncode==2
 print('PASS actual strict default-off Darwin GE8300 gate and all-writer ordering after NIR re-gather (ASan/UBSan)')
 # Compile the actual probe (not modeled API structs); source includes validated shader bytes.
 subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-fsyntax-only','-I',str(mesa/'include'),
   str(user/'powervrprobed_vulkan.c')],check=True)
 print('PASS full shipped Vulkan probe host syntax')
 if a.lavapipe:
  if 'libvulkan_lvp.so' not in a.lavapipe.name:raise SystemExit('Only explicit CPU lavapipe library is accepted')
  harness=t/'main.c';harness.write_text(r'''
#include <stdio.h>
#include <stdarg.h>
#include <stdlib.h>
static unsigned failures;
void probe_line(const char*f,...){va_list a;va_start(a,f);vprintf(f,a);va_end(a);puts("");}
void probe_pass(void){}void probe_fail(void){failures++;}
extern int probe_vulkan(const char*);
int main(int argc,char**argv){if(argc!=2)return 2;setenv("PVR_PROBE_ROA_COLOR","1",1);
return probe_vulkan(argv[1])!=0 || failures!=0;}
''')
  binary=t/'probe'
  subprocess.run(['cc','-std=gnu11','-Wall','-Wextra','-Werror','-I',str(mesa/'include'),str(harness),
    str(user/'powervrprobed_vulkan.c'),'-ldl','-o',str(binary)],check=True)
  subprocess.run([str(binary),str(a.lavapipe.resolve())],check=True,timeout=60)
  print('PASS CPU lavapipe reference only; not GE8300 or UI validation')
