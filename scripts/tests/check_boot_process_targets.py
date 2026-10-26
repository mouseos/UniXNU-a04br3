#!/usr/bin/env python3
"""Actual BootProcessProbe name/trace/follow matching, no XNU or process IO."""
from pathlib import Path
import os, re, subprocess, tempfile
root=Path(__file__).resolve().parents[2]
source=(root/'package/BootProcessProbe.c').read_text()
def function(signature):
 start=source.index(signature);at=source.index('{',start);end=at+1;depth=1
 while depth:
  depth+=(source[end]=='{')-(source[end]=='}');end+=1
 return source[start:end]
h=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
struct proc {const char *name;int pid;};typedef struct proc *proc_t;
static const char *proc_best_name(proc_t p){return p->name;}
static int proc_pid(proc_t p){return p->pid;}
static int follow_pid[2];
'''
for declaration in ('static unsigned trace_slot', 'static const char *targets'):
 h+=re.search(re.escape(declaration)+r'[^;]+;',source).group()+'\n'
for signature in ('static int followed_slot(', 'static unsigned capture_slot(', 'static int selected('):h+=function(signature)+'\n'
h+=r'''
static void check(const char *name,int pid,unsigned expected){
 struct proc p={name,pid};assert(capture_slot(&p)==expected);assert(selected(&p,NULL)==(expected<10));
}
int main(void){
 check("launchd",1,0);check("backboardd",56,1);check("SpringBoard",28,2);
 check("runningboardd",27,4);check("unlisted",80,10);check("kernel_task",0,9);
 targets[4]="launchd";trace_slot[4]=1;
 check("launchd",1,4);assert(trace_slot[4]); // p12 regression: role U is traced.
 targets[3]="keybagd";trace_slot[3]=1;check("keybagd",34,3);
 // Equal traced matches keep first-match priority, without duplicate traversal.
 targets[2]="launchd";check("launchd",1,2);
 targets[2]="SpringBoard";
 // Follow reservations take precedence, retaining the untraced duplicate row.
 follow_pid[0]=80;check("launchd",1,0);check("unlisted",80,4);
 follow_pid[1]=81;check("unlisted",81,1);check("backboardd",56,10);
 // Stable slots 2/3 are never redirected by a follow pid.
 check("SpringBoard",80,2);check("keybagd",81,3);
 // A focus alias does not replace kernel_task's pageout summary role.
 targets[4]="kernel_task";follow_pid[0]=follow_pid[1]=0;
 check("kernel_task",0,9);
 // Untraced duplicates retain original summary priority.
 trace_slot[3]=0;targets[3]="launchd";check("launchd",1,0);
 puts("PASS actual target matching: duplicate focus, traced priority, stable ties, follow reservations/overrides, protected roles, kernel summary");
}
'''
assert 'unsigned index=capture_slot(p);' in function('static int capture(')
with tempfile.TemporaryDirectory(prefix='boot-process-targets-') as tmp:
 src=Path(tmp)/'test.c';src.write_text(h);binary=src.with_suffix('')
 subprocess.run([os.environ.get('CC','cc'),'-std=c11','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',str(src),'-o',str(binary)],check=True)
 subprocess.run([str(binary)],check=True,timeout=30)
