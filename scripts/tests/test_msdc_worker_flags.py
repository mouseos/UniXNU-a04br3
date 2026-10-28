#!/usr/bin/env python3
"""Execute the actual build-script worker gate over all prerequisite combinations."""
import ast, os
from pathlib import Path
from types import SimpleNamespace
src=(Path(__file__).resolve().parents[1]/'build-arm-driver.py').read_text()
tree=ast.parse(src)
node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
          "XNU_MSDC_IRQ_WORKER" in ast.get_source_segment(src,n.test))
code=compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),__file__,'exec')
ns_node=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and
             'XNU_MSDC_GIC_NS' in ast.get_source_segment(src,n.test))
ns_code=compile(ast.fix_missing_locations(ast.Module(body=[ns_node],type_ignores=[])),__file__,'exec')
class Reject(Exception):pass
class Parser:
 def error(self, message):raise Reject(message)
old=dict(os.environ)
try:
 for worker in (0,1):
  for irq in (0,1):
   for block in (0,1):
    for verified in (0,1):
     os.environ['XNU_MSDC_IRQ_WORKER']=str(worker);os.environ['XNU_MSDC_IRQ_WAIT']=str(irq)
     command=[];ctx={'os':os,'parser':Parser(),'block_devices':bool(block),'args':SimpleNamespace(verified_root=bool(verified)),'command':command}
     try:exec(code,ctx);rejected=False
     except Reject:rejected=True
     expected=bool(worker and not(irq and block and verified))
     assert rejected==expected,(worker,irq,block,verified)
     assert bool(command)==bool(worker and not expected)
     if command:assert command==['-DNATIVE_MSDC_WORKER_EXPERIMENT=1']
 for ns in (0,1):
  for irq in (0,1):
   os.environ['XNU_MSDC_GIC_NS']=str(ns);os.environ['XNU_MSDC_IRQ_WAIT']=str(irq)
   command=[];ctx={'os':os,'parser':Parser(),'component':'ArmCpu','command':command}
   try:exec(ns_code,ctx);rejected=False
   except Reject:rejected=True
   assert rejected==bool(ns and not irq),(ns,irq)
   assert command==(['-DARMCPU_GICV2_ASSUME_NONSECURE=1'] if ns and irq else [])
finally:
 os.environ.clear();os.environ.update(old)
print('PASS actual build gate: opt-in, IRQ, block and verified-root prerequisites')
