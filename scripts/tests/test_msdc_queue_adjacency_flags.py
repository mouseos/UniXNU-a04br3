#!/usr/bin/env python3
"""Exercise the actual NativeMsdc builder's adjacency option gate."""
import ast
from itertools import product
from pathlib import Path
from types import SimpleNamespace
s = (Path(__file__).resolve().parents[1] / 'build-arm-driver.py').read_text()
tree = ast.parse(s)
parent = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and isinstance(n.test, ast.Compare)
              and ast.unparse(n.test) == "component == 'NativeMsdc'")
assign = next(i for i, n in enumerate(parent.body) if isinstance(n, ast.Assign) and
              any(isinstance(t, ast.Name) and t.id == 'adjacency' for t in n.targets))
body = parent.body[assign:assign+3]
code = compile(ast.fix_missing_locations(ast.Module(body=body, type_ignores=[])), __file__, 'exec')
class Parser:
 def error(self, message): raise ValueError(message)
def run(value, irq, worker, block, verified):
 command=[]
 env={'XNU_MSDC_IRQ_WAIT':irq,'XNU_MSDC_IRQ_WORKER':worker}
 if value is not None: env['XNU_MSDC_QUEUE_ADJACENCY']=value
 exec(code, {'os':SimpleNamespace(environ=env),'parser':Parser(),'command':command,
             'block_devices':block,'args':SimpleNamespace(verified_root=verified)})
 return command
assert run(None,'0','0',False,False)==[]
assert run('0','0','0',False,False)==[]
for irq, worker, block, verified in product(('0','1'),('0','1'),(False,True),(False,True)):
 try: result=run('1',irq,worker,block,verified)
 except ValueError: assert (irq,worker,block,verified)!=('1','1',True,True)
 else: assert (irq,worker,block,verified)==('1','1',True,True) and result==['-DNATIVE_MSDC_QUEUE_ADJACENCY=1']
for invalid in ('', '2', 'yes'):
 try: run(invalid,'1','1',True,True)
 except ValueError: pass
 else: raise AssertionError(invalid)
print('PASS actual NativeMsdc option gate, default off and prerequisites')
