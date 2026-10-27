#!/usr/bin/env python3
"""入力・RTCの実関数をMMIOモックで実行し、失敗時の隔離を確認。"""
from pathlib import Path
import subprocess
import tempfile
root = Path(__file__).resolve().parents[2]
def definition(src, sig):
    start=src.index(sig); pos=src.index('{',start); depth=1; end=pos+1
    while depth:
        depth+=(src[end]=='{')-(src[end]=='}'); end+=1
    return src[start:end]
inp=(root/'package/A04BR3Input.cpp').read_text()
rtc=(root/'package/A04BR3AutoReset.cpp').read_text()
code=r'''
#include <cassert>
#include <cstdint>
uint8_t buffer[8]; volatile uint8_t *gPwrap=buffer;
constexpr unsigned kWacsRdata=0xa4,kWacsCmd=0xa0,kWacsVldclr=0xa8;
unsigned fsm=0,commands=0,acks=0,poisons=0,mode=0;
bool init=true;
uint32_t rd(volatile uint8_t*,unsigned off){
 assert(off==0xa4); if(!init)return 0;
 unsigned current=fsm;
 if(mode==1 && commands==0)current=1;
 if(mode==2 && commands>0 && acks==0)current=1;
 if(mode==3 && acks>0)current=1;
 return (1u<<21)|(current<<16)|0x1234;
}
void wr(volatile uint8_t*,unsigned off,uint32_t v){
 if(off==0xa0){++commands;fsm=(v>>31)?0:6;}
 else {assert(off==0xa8&&v==1);++acks;fsm=0;}
}
void IODelay(unsigned){}
void a04br3_wacs_poison(){++poisons;}
void reset(unsigned m){fsm=commands=acks=poisons=0;mode=m;init=true;}
'''
for sig in ['int pwWait(', 'int pwRead(', 'int pwWrite(']: code+=definition(inp,sig)+'\n'
code+=definition(rtc,'bool wacs(')+'\n'
code+=r'''
int main(){
 uint16_t val=0; uint32_t val32=0;
 reset(0);assert(!pwRead(0x530,&val)&&val==0x1234&&commands==1&&acks==1&&!poisons);
 for(unsigned m: {1u,2u,3u}){reset(m);assert(pwRead(0x530,&val)!=0&&poisons==1);}
 reset(2);assert(pwWrite(0x530,0x60)!=0&&poisons==1);
 reset(0);init=false;assert(pwRead(0x530,&val)!=0&&poisons==1&&!commands);
 reset(0);assert(wacs(false,0x802c,0,&val32)&&val32==0x1234&&acks==1&&!poisons);
 for(unsigned m: {1u,2u}){reset(m);assert(!wacs(false,0x802c,0,&val32)&&poisons==1);}
 reset(0);init=false;assert(!wacs(true,0x8036,0,nullptr)&&poisons==1&&!commands);
 // The asynchronous RTC write completes at the next idle wait. A failed wait
 // must quarantine the previous write even before a new command is issued.
 reset(0);assert(wacs(true,0x8036,1,nullptr));mode=1;commands=0;
 assert(!wacs(false,0x8000,0,&val32)&&poisons==1);
}
'''
code=code.replace('#include <cassert>', '#include <cassert>\n#include <initializer_list>')
with tempfile.TemporaryDirectory(prefix='a04br3-wacs-clients-') as tmp:
    p=Path(tmp);(p/'test.cpp').write_text(code)
    subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror',str(p/'test.cpp'),'-o',str(p/'test')],check=True)
    subprocess.run([str(p/'test')],check=True)
print('PASS: 実入力・RTCのWACS待機、timeoutとINIT_DONE消失時の隔離')
