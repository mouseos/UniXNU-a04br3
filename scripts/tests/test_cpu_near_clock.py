#!/usr/bin/env python3
"""Compile the actual hook body for Darwin arm64 and inspect basic codegen."""
import pathlib,re,shutil,subprocess,tempfile
source=(pathlib.Path(__file__).resolve().parents[2]/'package/A04BR3Board.cpp').read_text()
start=source.index('extern "C" bool a04br3_cpu_clock_near(');end=source.index('\n}\n',start)+3
prefix='typedef unsigned long long uint64_t; typedef unsigned int uint32_t; typedef unsigned char uint8_t; extern volatile uint8_t *cpuContextInfra, *cpuContextApmixed;\n'
objdump=shutil.which('llvm-objdump') or '/usr/lib/llvm-18/bin/llvm-objdump'
with tempfile.TemporaryDirectory() as d:
 p=pathlib.Path(d);(p/'hook.cpp').write_text(prefix+source[start:end]);subprocess.run(['clang','-target','arm64-apple-ios17.0','-O2','-c',str(p/'hook.cpp'),'-o',str(p/'hook.o')],check=True)
 text=subprocess.check_output([objdump,'--disassemble-symbols=_a04br3_cpu_clock_near',str(p/'hook.o')],text=True)
 lines=text.splitlines();reads=[i for i,x in enumerate(lines) if re.search(r'\bmrs\s+x\d+,\s*CNTVCT_EL0',x,re.I)]
 assert len(reads)==2 and all(re.search(r'\bisb\b',lines[i-1],re.I) for i in reads)
 assert len(re.findall(r'\bldr\s+w\d+,',text,re.I))>=5 and re.search(r'\bdmb\s+oshld',text,re.I) and not re.search(r'\bbl\b',text,re.I)
print('PASS actual near hook Darwin arm64 codegen')
