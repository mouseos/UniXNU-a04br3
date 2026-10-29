#!/usr/bin/env python3
import importlib.util
from pathlib import Path
p=Path(__file__).resolve().parents[1]/'summarize-pmu-context.py';spec=importlib.util.spec_from_file_location('context',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
rows=[{'round':2,'cpu':1,'status':0,'begin':100,'end':200}]
def base(phase,b,e):return f'a04br3 cpu-context round=2 cpu=1 phase={phase} begin={b} end={e} clock=1 load=1 rail=unknown temp=unknown\n'
def clk(phase):return f'a04br3 cpu-context-clk round=2 cpu=1 phase={phase} infra=00000004 dcm=00000000 con0=00000011 con1=0009a000 pll_pwr=00000005\n'
def load(phase):return f'a04br3 cpu-context-load round=2 cpu=1 phase={phase} user=1 system=2 idle=3 nice=4 state=6/6 ticks32=1\n'
before=base(0,80,90)+clk(0)+load(0);after=base(1,210,220)+clk(1)+load(1);good=m.join_context(before+after,rows);assert not good['issues'] and good['context_count']==2 and good['records'][0]['before']['clock']['con1']=='0009a000'
for bad in (before,before+after+base(1,210,220),base(0,80,110)+clk(0)+load(0)+after,before+base(1,190,220)+clk(1)+load(1),base(0,80,90)+load(0)+after):assert m.join_context(bad,rows)['issues']
print('PASS context join, pre/post order, duplicates, missing and availability')
