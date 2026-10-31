#!/usr/bin/env python3
"""App launch breakdown of one a04br3 boot with a04br3-input-latency=2 (package/BootProcessProbe.c, A04BR3AutoReset.cpp).

For each timed tap (A04BR3!TAP, scripts/a04br3-input-latency.py --app) the kernel prints "a04br3 lt" lines from the tap's UART
receipt until its latency sample closes. This script turns them, with the latency result line, the backboardd render events of
the Metal plug-in (UIL, PVR_METAL_LATENCY_DIAG: e10 = a render command buffer commit, its mach tick at 1000/13 ns) and the
"MetalPluginName read by Preferences" line into:
  - a timeline in ms after the tap: received, handed to iPadOS, first swap, Preferences spawned (posix_spawn by launchd on the
    launch request), its pthread registration (libSystem's initializer: dyld finished binding), its second thread, its Metal
    plug-in read (UIKit/Metal start, bracketed by the neighbouring samples), the first backboardd render commit after the
    spawn, the first present and the last frame;
  - one row per second: page-ins, faults, decompressions, eMMC reads (bytes, ms, calls <= 4 KiB), GPU fence waits and
    timeouts, free pages and the CPU of Preferences, SpringBoard and backboardd;
  - thread-seconds per wait (continuation or saved kernel return address, symbolized with --kernel and the printed slide) for
    Preferences, SpringBoard and backboardd. A thread not reprinted keeps its last printed state (the kernel prints only threads
    that ran >= 1 ms or changed their wait).

usage: a04br3-launch-breakdown.py --log UART_LOG --from-offset A [--to-offset B] [--kernel KERNEL.macho] [--seq N] --out OUT.json
"""
import argparse, bisect, collections, json, re, subprocess, sys

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--log', required=True)
p.add_argument('--from-offset', type=int, required=True)
p.add_argument('--to-offset', type=int)
p.add_argument('--kernel', help='the booted kernel (Mach-O with symbols) for wait symbols')
p.add_argument('--seq', type=int, help='only this tap sample (default: every one)')
p.add_argument('--out', required=True)
a = p.parse_args()

with open(a.log, 'rb') as f:
    f.seek(a.from_offset)
    data = f.read(a.to_offset - a.from_offset) if a.to_offset else f.read()
lines = data.decode('latin1').replace('\r', '').split('\n')

syms = []
if a.kernel:
    for tool in ('llvm-nm-18', 'llvm-nm', 'nm'):
        try:
            out = subprocess.run([tool, '-n', '--defined-only', a.kernel], capture_output=True, text=True, check=True).stdout
            break
        except (OSError, subprocess.CalledProcessError):
            out = ''
    for l in out.splitlines():
        parts = l.split()
        if len(parts) >= 3 and parts[1] in ('T', 't', 'S', 's'):   # the kernel's text symbols are section (S) symbols
            syms.append((int(parts[0], 16), parts[2]))
    syms.sort()
addrs = [s[0] for s in syms]

def sym(addr, slide):
    if not addr or not syms:
        return hex(addr) if addr else ''
    u = addr - slide
    i = bisect.bisect_right(addrs, u) - 1
    if i < 0 or u - addrs[i] > 0x10000:
        return hex(addr)
    return syms[i][1].lstrip('_')

KV = re.compile(r'(\w+)=([-\w/.]+)')
BEGIN = re.compile(r'a04br3 lt begin rx_ns=(\d+) kslide=([0-9a-f]+)')
LAT = re.compile(r'a04br3 latency: seq=(\d+) kind=tap (ok|timeout) rx_ns=(\d+) hid_ms=([\d.]+) swap_ms=([\d.]+) present_ms=([\d.]+)')
TAP = re.compile(r'a04br3 latency: seq=(\d+) tap frames=(\d+) last_ms=([\d.]+) times_ms=([\d,]*)')
UIL = re.compile(r'UIL p(\d+) e(\d+) i(\d+) t([0-9a-f]+)')
TH = re.compile(r'a04br3 lt th t_us=(-?\d+) p(\d+) t(\d+) s=([0-9a-f]+) pri=(-?\d+) cpu_us=(\d+) e=([0-9a-f]+) c=([0-9a-f]+) u=([0-9a-f]+) k=([0-9a-f]+)/([0-9a-f]+)/([0-9a-f]+)')

samples, cur = [], None
lat = {}
uil = []
for n, l in enumerate(lines):
    m = BEGIN.search(l)
    if m:
        cur = dict(rx_ns=int(m[1]), slide=int(m[2], 16), ev=[], s=[], p=[], th=[], top=[], metal=[], last_t=0, begin_line=n)
        samples.append(cur)
        continue
    m = UIL.search(l)
    if m:
        uil.append((int(m[4], 16) * 1000 // 13, int(m[1]), int(m[2]), int(m[3])))
    m = LAT.search(l)
    if m:
        lat[int(m[3])] = dict(seq=int(m[1]), status=m[2], hid_ms=float(m[4]), swap_ms=float(m[5]), present_ms=float(m[6]))
    m = TAP.search(l)
    if m:
        for v in lat.values():
            if v['seq'] == int(m[1]):
                v.update(frames=int(m[2]), last_ms=float(m[3]), times_ms=[int(x) for x in m[4].split(',') if x])
    if cur is None:
        continue
    if 'a04br3 lt end' in l:
        cur['end_line'] = n; cur = None; continue
    if 'MetalPluginName read by Preferences' in l:
        cur['metal'].append(cur['last_t'])
    mm = re.search(r'MetalPluginName read by (\S+) pid=(\d+)', l)
    if mm:
        cur.setdefault('metal_all', []).append((cur['last_t'], mm[1], int(mm[2])))
    if not l.startswith('a04br3 lt ') and ' a04br3 lt ' not in l:
        continue
    kv = dict(KV.findall(l))
    t = int(kv.get('t_us', cur['last_t']))
    cur['last_t'] = t
    if ' lt ev ' in l:
        cur['ev'].append((t, l.split(' lt ev ', 1)[1].split(' ', 1)[1]))
    elif ' lt s ' in l:
        cur['s'].append({k: (int(v) if v.lstrip('-').isdigit() else v) for k, v in kv.items()} | {'raw': l})
    elif ' lt p ' in l:
        mm = re.search(r'lt p t_us=-?\d+ (\S+) p(\d+)', l)
        cur['p'].append(dict(t=t, name=mm[1], pid=int(mm[2]), **{k: int(v) for k, v in kv.items() if v.isdigit()}))
    elif ' lt top ' in l:
        mm = re.search(r'lt top t_us=-?\d+ p(\d+) (\S+)( new)? cpu_ms=(\d+) faults=(\d+) pageins=(\d+)', l)
        if mm:
            cur['top'].append(dict(t=t, pid=int(mm[1]), name=mm[2], new=bool(mm[3]), cpu_ms=int(mm[4]), faults=int(mm[5]),
                                   pageins=int(mm[6])))
    elif ' lt th ' in l:
        mm = TH.search(l)
        if mm:
            cur['th'].append(dict(t=t, pid=int(mm[2]), tid=int(mm[3]), state=int(mm[4], 16), pri=int(mm[5]), cpu_us=int(mm[6]),
                                  event=int(mm[7], 16), cont=int(mm[8], 16), pc=int(mm[9], 16),
                                  k=[int(mm[10], 16), int(mm[11], 16), int(mm[12], 16)]))

def wait_name(th, slide):
    if th['state'] & 4:   # TH_RUN
        return 'running'
    if th['cont']:
        return 'cont:' + sym(th['cont'], slide)
    names = [sym(k, slide) for k in th['k'] if k]
    skip = ('thread_block', 'thread_invoke', 'Switch_context', 'machine_switch_context', 'thread_block_reason', 'thread_block_parameter')
    rest = [s for s in names if not any(s.startswith(x) or s.endswith(x) for x in skip)]
    return '/'.join((rest or names)[:2]) or ('event' if th['event'] else 'unknown')

result = []
for smp in samples:
    rx = smp['rx_ns']
    L = lat.get(rx, {})
    if a.seq and L.get('seq') != a.seq:
        continue
    ms = lambda us: round(us / 1000, 1)
    ev = {}
    for t, text in smp['ev']:
        key = text.split()[0]
        ev.setdefault(key, (ms(t), text))
    pref_pid = None
    for t, text in smp['ev']:
        mm = re.search(r'pref_seen pid=(\d+) spawn_us=(-?\d+)', text)
        if mm:
            pref_pid = int(mm[1]); spawn = ms(int(mm[2])); break
    timeline = [('tap received (UART)', 0.0)]
    if L:
        timeline += [('handed to iPadOS (touch path)', L['hid_ms']), ('first swap after the tap', L['swap_ms'])]
    if pref_pid:
        timeline.append(('Preferences spawned (launch request served by launchd)', spawn))
        timeline.append(('Preferences seen by the 100 ms poll', ev['pref_seen'][0]))
    if 'pref_pthread_registered' in ev:
        timeline.append(('Preferences pthread registered (dyld done, libSystem initializer)', ev['pref_pthread_registered'][0]))
    if 'pref_threads' in ev:
        timeline.append(('Preferences second thread', ev['pref_threads'][0]))
    if smp['metal']:
        timeline.append(('Preferences reads MetalPluginName (UIKit/Metal start; after the sample at)', ms(smp['metal'][0])))
    if pref_pid is not None:
        spawn_ns = rx + int(spawn * 1e6)
        bb = [u for u in uil if u[2] == 10 and u[0] >= spawn_ns]
        if bb:
            timeline.append(('first backboardd render commit (UIL e10) after the spawn', round((bb[0][0] - rx) / 1e6, 1)))
    first_e10 = [u for u in uil if u[2] == 10 and u[0] >= rx]
    if first_e10:
        timeline.append(('first backboardd render commit (UIL e10) after the tap', round((first_e10[0][0] - rx) / 1e6, 1)))
    if L.get('present_ms'):
        timeline.append(('first present', L['present_ms']))
    if L.get('last_ms'):
        timeline.append(('last frame before 15 s quiet', L['last_ms']))
    # iconservicesagent (icons for SpringBoard and the apps): a new pid is a restart after its previous instance ended
    # (Chandrasekhar, white-content-20261031.md: SIGSEGV at address 0). Its first Metal plug-in read and its "lt top ... new"
    # sighting give the restart time to the 100 ms / 1 s sample before the line.
    seen_pid = set()
    for t, name, pid in smp.get('metal_all', []):
        if name.startswith('iconservicesagen') and pid not in seen_pid:
            seen_pid.add(pid); timeline.append((f'iconservicesagent p{pid} reads MetalPluginName (start/restart)', ms(t)))
    for x in smp['top']:
        if x['name'].startswith('iconservicesagen') and x['new'] and x['pid'] not in seen_pid:
            seen_pid.add(x['pid']); timeline.append((f"iconservicesagent p{x['pid']} new (restart)", ms(x['t'])))
    timeline.sort(key=lambda x: x[1])
    cpu = collections.defaultdict(dict)
    for r in smp['p']:
        cpu[r['t']][r['name']] = r
    per_second = []
    for s in smp['s']:
        row = dict(t_s=round(s['t_us'] / 1e6, 1), pageins=s['pageins'], faults=s['faults'], decomp=s['decomp'], free=s['free'],
                   rd_mb=round(s['rd_bytes'] / 1048576, 2), rd_ms=s['rd_ms'], rd_calls=s['rd_calls'], rd_le4k=s['rd_le4k'])
        fo = re.search(r'fence_other=(\d+)/(\d+) ms=(\d+) fence_bb=(\d+)/(\d+) ms=(\d+) fence_bb_ok=(\d+) timeout=(\d+) fence_other_ok=(\d+) timeout=(\d+)', s['raw'])
        if fo:
            row.update(fence_bb=int(fo[4]), fence_bb_ms=int(fo[6]), fence_bb_timeout=int(fo[8]),
                       fence_other=int(fo[1]), fence_other_ms=int(fo[3]), fence_other_timeout=int(fo[10]))
        for name in ('Preferences', 'SpringBoard', 'backboardd', 'runningboardd', 'launchd', 'lsd'):
            r = cpu.get(s['t_us'], {}).get(name)
            if r:
                row[name + '_cpu_ms'] = r['cpu_ms']; row[name + '_pageins'] = r['pageins']; row[name + '_faults'] = r['faults']
        per_second.append(row)
    # Thread-seconds per wait: carry each thread's last printed state across the 1 s samples.
    pids = {r['pid']: r['name'] for r in smp['p']}
    full_times = sorted({r['t'] for r in smp['p']})
    by_t = collections.defaultdict(list)
    for th in smp['th']:
        by_t[th['t']].append(th)
    state = {}
    waits = collections.defaultdict(lambda: collections.Counter())
    end = (L.get('present_ms') or 0) * 1000
    for i, t in enumerate(full_times):
        for th in by_t.get(t, []):
            state[(th['pid'], th['tid'])] = th
        nxt = full_times[i + 1] if i + 1 < len(full_times) else t
        dt = (nxt - t) / 1e6
        if dt <= 0:
            continue
        for (pid, tid), th in state.items():
            name = pids.get(pid, str(pid))
            w = wait_name(th, smp['slide'])
            waits[name][w] += dt
            if end and t < end:
                waits[name + ' (to first present)'][w] += dt
    # Every process that worked between the tap and the first present (all processes, "a04br3 lt top").
    limit = (L.get('present_ms') or 1e9) * 1000
    tops = collections.defaultdict(lambda: dict(cpu_ms=0, faults=0, pageins=0, new_at_ms=None))
    for x in smp['top']:
        if x['t'] > limit + 1100000:
            continue
        k = f"{x['name']} p{x['pid']}"
        tops[k]['cpu_ms'] += x['cpu_ms']; tops[k]['faults'] += x['faults']; tops[k]['pageins'] += x['pageins']
        if x['new'] and tops[k]['new_at_ms'] is None:
            tops[k]['new_at_ms'] = ms(x['t'])
    top = sorted(tops.items(), key=lambda kv: -(kv[1]['cpu_ms'] + kv[1]['pageins']))[:25]
    result.append(dict(seq=L.get('seq'), top_to_present=top, rx_ns=rx, latency=L, preferences_pid=pref_pid,
                       timeline=[dict(event=e, ms=v) for e, v in timeline], per_second=per_second,
                       waits={k: [(w, round(s, 1)) for w, s in c.most_common(12)] for k, c in waits.items()},
                       events=[dict(ms=ms(t), text=x) for t, x in smp['ev']]))
json.dump(result, open(a.out, 'w'), indent=1)
for r in result:
    print(f"seq={r['seq']} pid={r['preferences_pid']}")
    for e in r['timeline']:
        print(f"  {e['ms']:>9} ms  {e['event']}")

