#!/usr/bin/env python3
"""Input-to-display latency bench for a running a04br3 kernel with a04br3-input-latency=1 (package/LegacyDisplayProbe.cpp).

The host sends inputs over UART0 (A04BR3AutoReset.cpp): "A04BR3!HOME" (the Home key, through NativeHidInput) and "A04BR3!SWR" (one
rightward swipe through the touch path, package/A04BR3Input.cpp). The kernel stamps each input when it is received, when it is
handed to iPadOS, at the first swap the compositor submits and at the first frame completed into the scanout, and prints one
"a04br3 latency:" line per input. Times are the kernel's; the UART only carries the request and the result.

Sequence: wait until the display has presented a frame (the lock screen), wait --settle seconds, send Home once to wake and unlock
(reported, not counted), then alternate swipe (home screen -> widget view) and Home (widget view -> home screen) --count times,
each --gap seconds after the previous input's result line. Every input changes the screen, so the first new frame is the response.
The UART collector stays the only reader; this script only writes the requests and reads the collector's log.

usage: a04br3-input-latency.py --log UART_LOG [--from-offset N] [--uart /dev/ttyUSB0] [--count 10] [--gap 17] [--settle 60] --out RESULT.json
       a04br3-input-latency.py --log UART_LOG --from-offset N --parse-only --out RESULT.json     (summarize an existing log)
"""
import argparse, json, os, re, statistics, sys, time

p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
p.add_argument('--log', required=True)
p.add_argument('--from-offset', type=int, help='byte offset of this boot in the log (default: the end of the log now)')
p.add_argument('--uart', default='/dev/ttyUSB0')
p.add_argument('--count', type=int, default=10)
p.add_argument('--gap', type=float, default=17, help='seconds between a result line and the next input (not a multiple of 60)')
p.add_argument('--settle', type=float, default=60)
p.add_argument('--ready-timeout', type=float, default=1500)
p.add_argument('--result-timeout', type=float, default=75)
p.add_argument('--parse-only', action='store_true')
p.add_argument('--out', required=True)
a = p.parse_args()

LINE = re.compile(r'a04br3 latency: seq=(\d+) kind=(\w+) (ok|timeout) rx_ns=(\d+) hid_ms=([\d.]+) swap_ms=([\d.]+) '
                  r'present_ms=([\d.]+) frames_2s=(\d+) idle_before_ms=(\d+)')
PRESENTED = re.compile(r'a04br3 display: stage .* presented (\d+)')
start = a.from_offset if a.from_offset is not None else os.path.getsize(a.log)

def text():
    with open(a.log, 'rb') as f:
        f.seek(start)
        return f.read().decode('latin1').replace('\r', '')

def results():
    out = []
    for m in LINE.finditer(text()):
        out.append(dict(seq=int(m[1]), kind=m[2], status=m[3], rx_ns=int(m[4]), hid_ms=float(m[5]), swap_ms=float(m[6]),
                        present_ms=float(m[7]), frames_2s=int(m[8]), idle_before_ms=int(m[9])))
    return out

def send(request):
    with open(a.uart, 'wb', buffering=0) as port:
        port.write(b'\n' + request + b'\n')
    print(time.strftime('%H:%M:%S'), 'sent', request.decode(), flush=True)

def wait_result(seen):
    deadline = time.time() + a.result_timeout
    while time.time() < deadline:
        r = results()
        if len(r) > seen: return r
        time.sleep(1)
    return results()

def summarize(samples):
    def stats(values):
        return dict(n=len(values), median=round(statistics.median(values), 3) if values else None,
                    max=round(max(values), 3) if values else None, min=round(min(values), 3) if values else None)
    # samples[0] is the wake/unlock Home. A sample answers its input only when a swap followed the input and the first frame came
    # after it: kernels before 874a08c's successor stamped the first frame after the input even if it belonged to an earlier swap.
    for s in samples:
        s['valid'] = s['status'] == 'ok' and s['swap_ms'] > 0 and s['present_ms'] >= s['swap_ms']
    measured = [s for s in samples[1:] if s['valid']]
    out = dict(log=a.log, from_offset=start, unlock=samples[0] if samples else None, samples=samples[1:],
               timeouts=sum(1 for s in samples[1:] if s['status'] != 'ok'),
               unattributed=sum(1 for s in samples[1:] if s['status'] == 'ok' and not s['valid']),
               present_ms=stats([s['present_ms'] for s in measured]), swap_ms=stats([s['swap_ms'] for s in measured]),
               hid_ms=stats([s['hid_ms'] for s in measured]),
               present_ms_timeouts_as_60s=stats([s['present_ms'] if s['valid'] else 60000.0 for s in samples[1:] if s['valid'] or s['status'] != 'ok']),
               present_after_hid_ms=stats([s['present_ms'] - s['hid_ms'] for s in measured]))
    for kind in ('home', 'swipe'):
        out['present_ms_' + kind] = stats([s['present_ms'] for s in measured if s['kind'] == kind])
    return out

if not a.parse_only:
    deadline = time.time() + a.ready_timeout
    while time.time() < deadline:
        t = text()
        counts = [int(m[1]) for m in PRESENTED.finditer(t)]
        if 'a04br3 latency: display ready' in t or (counts and max(counts) >= 1): break
        time.sleep(5)
    else:
        sys.exit('no presented frame in the log within --ready-timeout')
    print(time.strftime('%H:%M:%S'), 'display up; settling', a.settle, 's', flush=True)
    time.sleep(a.settle)
    seen = len(results())
    for i in range(a.count + 1):
        send(b'A04BR3!HOME' if i == 0 or i % 2 == 0 else b'A04BR3!SWR')
        r = wait_result(seen)
        if len(r) > seen:
            print(time.strftime('%H:%M:%S'), 'result', {k: r[-1][k] for k in ('seq', 'kind', 'status', 'present_ms')}, flush=True)
        else:
            print(time.strftime('%H:%M:%S'), 'no result line', flush=True)
        seen = len(r)
        time.sleep(a.gap)

summary = summarize(results())
json.dump(summary, open(a.out, 'w'), indent=1)
print(json.dumps({k: summary[k] for k in ('timeouts', 'unattributed', 'present_ms', 'present_ms_timeouts_as_60s', 'swap_ms', 'hid_ms', 'present_after_hid_ms', 'present_ms_home', 'present_ms_swipe')}, indent=1))
