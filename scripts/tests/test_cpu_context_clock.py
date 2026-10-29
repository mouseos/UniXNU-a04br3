#!/usr/bin/env python3
"""Compile the two actual clock asm statements for Darwin and check MRS."""
from pathlib import Path
import re,shutil,subprocess,tempfile
root=Path(__file__).resolve().parents[2];s=(root/'package/A04BR3Board.cpp').read_text();body=s[s.index('extern "C" void a04br3_cpu_context_sample'):s.index('// Diagnostic sampling only.')];asm=re.findall(r'__asm__ volatile\("([^"\n]+)"\s*:\s*"=r"\((begin|end)\)\s*::\s*"memory"\)',body);assert len(asm)==2 and {v for _,v in asm}=={'begin','end'}
clang=shutil.which('clang');objdump=shutil.which('llvm-objdump') or '/usr/lib/llvm-18/bin/llvm-objdump';assert clang and Path(objdump).exists()
with tempfile.TemporaryDirectory(prefix='cpu-context-asm-') as d:
 p=Path(d);src=p/'check.c';obj=p/'check.o';src.write_text('\n'.join('unsigned long long read_'+v+'(void){unsigned long long '+v+'; __asm__ volatile("'+a+'" : "=r"('+v+') :: "memory");return '+v+';}' for a,v in asm))
 subprocess.run([clang,'-target','arm64-apple-ios17.0','-O2','-c',str(src),'-o',str(obj)],check=True)
 text=subprocess.check_output([objdump,'--disassemble',str(obj)],text=True)
 assert len(re.findall(r'\bmrs\s+x\d+,\s*CNTVCT_EL0\b',text,re.I))==2,text
print('PASS Darwin arm64 actual source clock statements: two CNTVCT MRS instructions')
