"""Pure UART snapshot and service continuity checks; no device access."""
import hashlib
import re

ANCHOR = b'[pmic6392_init] Preloader Start'
PANIC = re.compile(rb'(?i)(?:panic[ \t]*(?:\(|:)|kernel panic)')
EL3 = re.compile(rb'(?i)(?:EL3[^\r\n]{0,60}(?:exception|abort|serror)|(?:exception|abort|serror)[^\r\n]{0,60}EL3|\bESR_EL3[ \t]*[:=])')
REASON = re.compile(rb'(?i)\bBOOT_REASON[ \t]*[:=][ \t]*(0[xX][0-9a-fA-F]{1,16}|[0-9]{1,20})[ \t\r]*\n')


def continuity(before, after):
    """Detect service replacement/restart and file replacement/truncation, not serial byte loss."""
    keys = ('invocation', 'main_pid', 'restarts', 'dev', 'inode')
    if any(before.get(k) != after.get(k) for k in keys):
        raise ValueError('UART service or file identity changed')
    if not before.get('invocation') or not before.get('main_pid') or before.get('active') != 'active' or after.get('active') != 'active':
        raise ValueError('UART service inactive')
    if not isinstance(before.get('size'), int) or not isinstance(after.get('size'), int) or after['size'] < before['size']:
        raise ValueError('UART truncated')
    return {'status':'continuous_identity', 'before':before, 'after':after,
            'limitations':['UART byte loss and restart followed by data loss cannot be excluded']}


def events(data):
    def matches(pattern):
        return [{'offset': m.start(), 'line': data[data.rfind(b'\n',0,m.start())+1:data.find(b'\n',m.start()) if b'\n' in data[m.start():] else len(data)].decode('utf-8','replace')[:512]} for m in pattern.finditer(data)]
    return {'panic':matches(PANIC), 'el3':matches(EL3),
            'boot_reasons':[int(m.group(1), 16 if m.group(1).lower().startswith(b'0x') else 10) for m in REASON.finditer(data)]}


def classify(snapshot, cap, flash):
    """Recompute boundaries and retain old prefix and each subsequent boot separately."""
    base, completed = flash['uart_offset'], flash['flash_completed_uart_offset']
    if type(base) is not int or type(completed) is not int or not base <= completed <= base+len(snapshot):
        raise ValueError('invalid flash offsets')
    if cap.get('checkpoint_verified') is not True or cap.get('offset_crosses_preloader_anchor') is not False or cap.get('boots_truncated') is not False:
        raise ValueError('unsafe checkpoint or truncated boot list')
    if cap.get('snapshot_sha256') != hashlib.sha256(snapshot).hexdigest():
        raise ValueError('snapshot digest mismatch')
    positions=[m.start() for m in re.finditer(re.escape(ANCHOR), snapshot)]
    if len(positions)<2 or len(positions)!=len(cap.get('boots',[])) or positions[0] < completed-base:
        raise ValueError('missing or ambiguous post-flash boot boundary')
    prefix=snapshot[:positions[0]]
    old=prefix[:completed-base]; ambiguous=prefix[completed-base:]
    boot=[]
    for i,start in enumerate(positions):
        end=positions[i+1] if i+1<len(positions) else len(snapshot)
        raw=snapshot[start:end]; recorded=cap['boots'][i]
        if (recorded.get('index') != i+1 or recorded.get('start_offset') != base+start or recorded.get('end_offset') != base+end or recorded.get('sha256') != hashlib.sha256(raw).hexdigest() or recorded.get('closed_by_next_preloader') != (i+1<len(positions))):
            raise ValueError('boot range or digest mismatch')
        e=events(raw)
        boot.append({'index':i+1,'sha256':recorded['sha256'],'closed':i+1<len(positions),'events':e})
    for i in range(len(boot)-1): boot[i]['next_boot_reasons']=boot[i+1]['events']['boot_reasons']
    uncertain=events(ambiguous)
    clean=not uncertain['panic'] and not uncertain['el3'] and not uncertain['boot_reasons'] and not any(b['events']['panic'] or b['events']['el3'] for b in boot)
    return {'old_prefix':events(old), 'after_flash_prefix':uncertain, 'boots':boot, 'trial_exception_free':clean,
            'limitations':['flash completion offset is host observed; buffered UART data can cross the boundary', 'exception patterns are incomplete and UART byte loss is unproven']}


def service_snapshot(uart, unit, run):
    """Observe the configured user service and file identity without opening the serial device."""
    from pathlib import Path
    path=Path(uart)
    output=run(['systemctl','--user','show',unit,'-p','ActiveState','-p','MainPID','-p','InvocationID','-p','NRestarts'],text=True)
    props=dict(line.split('=',1) for line in output.splitlines() if '=' in line)
    config=run(['systemctl','--user','cat',unit],text=True)
    destinations=[line.split('=',1)[1][len('append:'):] for line in config.splitlines() if line.startswith('StandardOutput=append:')]
    if len(destinations)!=1 or Path(destinations[0]).resolve()!=path.resolve():
        raise ValueError('UART service output does not match capture file')
    st=path.stat()
    again=run(['systemctl','--user','show',unit,'-p','ActiveState','-p','MainPID','-p','InvocationID','-p','NRestarts'],text=True)
    props2=dict(line.split('=',1) for line in again.splitlines() if '=' in line)
    st2=path.stat()
    if props!=props2 or (st.st_dev,st.st_ino)!=(st2.st_dev,st2.st_ino) or st2.st_size<st.st_size:
        raise ValueError('UART service changed while sampling')
    return {'active':props['ActiveState'],'main_pid':int(props['MainPID']),'invocation':props['InvocationID'],
            'restarts':int(props['NRestarts']),'dev':st.st_dev,'inode':st.st_ino,'size':st.st_size}
