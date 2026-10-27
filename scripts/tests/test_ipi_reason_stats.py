#!/usr/bin/env python3
from pathlib import Path
import subprocess,tempfile
src=(Path(__file__).resolve().parents[2]/'package/BootProcessProbe.c').read_text()
start=src.index('static unsigned ipi_reason_enabled;'); end=src.index('\n}',src.index('void boot_ipi_reason_report(void)',start))+2
code='#include <stdint.h>\n#include <stdio.h>\n#include <assert.h>\n'+src[start:end]+r'''
int main(void){
 native_ipi_signal_record(0,1,8,0);assert(!ipi_reason_counts[0][1][3]);
 boot_ipi_reason_enable(1);native_ipi_signal_record(0,1,8,0);native_ipi_signal_record(0,1,0,1);
 native_ipi_signal_record(2,3,4,0);native_ipi_signal_record(3,0,0x200,0);native_ipi_signal_record(2,2,0x800,0);
 native_ipi_signal_record(4,0,4,0);native_ipi_signal_record(0,5,4,0);
 assert(ipi_reason_counts[0][1][3]==1 && ipi_reason_counts[0][1][0]==1 && ipi_reason_counts[0][1][6]==1);
 assert(ipi_reason_counts[2][3][2]==1 && ipi_reason_counts[3][0][4]==1 && ipi_reason_counts[2][2][5]==1 && ipi_reason_out_of_range==2);
 boot_ipi_reason_report();boot_ipi_reason_enable(0);native_ipi_signal_record(0,1,8,0);assert(ipi_reason_counts[0][1][3]==1);
}
'''
with tempfile.TemporaryDirectory() as d:
 p=Path(d);(p/'test.c').write_text(code);subprocess.run(['cc','-std=c11','-Wall','-Wextra','-Werror',str(p/'test.c'),'-o',str(p/'test')],check=True);subprocess.run([str(p/'test')],check=True)
print('PASS IPI reason actual code: off/on, signal categories, defer, target bounds, report and disable')
