"""Pure host-side interpretation of one bounded UART boot, never accesses devices."""
import re

PREFIX = 'a04br3_mem_copy'
SPAWN = re.compile(r'a04br3: memory bench spawn\s+(-?\d+)\s+\(pid\s+(-?\d+)\)')


def analyze_benchmark(raw, mode):
    if mode not in ('off', 'on'):
        raise ValueError('unknown mode')
    rows, errors = [], []
    spawns = [tuple(map(int, m.groups())) for m in SPAWN.finditer(raw)]
    for line in raw.splitlines():
        pos = line.find(PREFIX + ' ')
        if pos < 0:
            continue
        text = line[pos:]
        fields = {}
        for token in text.split()[1:]:
            key, sep, value = token.partition('=')
            if not sep or not key or key in fields:
                errors.append('malformed or duplicate field')
                continue
            fields[key] = value
        rows.append({'line': text, 'fields': fields})
    if mode == 'off':
        return {'valid': not rows and not spawns, 'outcome': 'disabled' if not rows and not spawns else 'unexpected_activity', 'rows': rows, 'spawns': spawns}
    def phase(name):
        return [r['fields'] for r in rows if r['fields'].get('phase') == name]
    waits, begins, ends, skips = (phase(x) for x in ('waiting', 'begin', 'end', 'skip'))
    result = {'valid': False, 'outcome': 'incomplete', 'rows': rows, 'spawns': spawns, 'errors': errors}
    if errors or len(spawns) != 1 or spawns[0][0] != 0 or spawns[0][1] <= 0 or len(waits) != 1 or waits[0].get('profile') != 'low_impact_64k' or waits[0].get('delay_seconds') != '180' or len(begins) != 1 or begins[0].get('kind') != 'copy' or begins[0].get('size_bytes') != '65536' or len(ends) != 1 or ends[0].get('kind') != 'copy':
        return result
    phases = [r['fields'].get('phase') for r in rows]
    if not phases.index('waiting') < phases.index('begin') < phases.index('end') or phases[-1] != 'end':
        return result
    if len(phase('prepare')) != 1 or not phases.index('begin') < phases.index('prepare') < phases.index('end'):
        return result
    try:
        usage = phase('vm_usage')[0]
        activity = phase('vm_activity')[0]
        check = phase('memory_check')[0]
        qb, qe = int(usage['query_begin_ns']), int(usage['query_end_ns'])
        if qb > qe or qb != int(activity['query_begin_ns']) or qe != int(activity['query_end_ns']):
            return result
        reported, speculative, free, threshold = (int(check[k]) for k in ('reported_free_bytes', 'speculative_bytes', 'free_bytes', 'threshold_bytes'))
        if min(reported, speculative, free) < 0 or reported - speculative != free or threshold != 2*65536 + 32*1024*1024:
            return result
    except (IndexError, KeyError, ValueError):
        return result
    # Preserve all raw VM observations; they are a non-atomic snapshot with unknown freshness.
    status = ends[0].get('status')
    if status == 'skip' and free < threshold and len(skips) == 1 and skips[0].get('reason') == 'insufficient_free_pages':
        if len(phase('vm_activity')) == len(phase('vm_usage')) == len(phase('memory_check')) == 1:
            result.update(valid=True, outcome='skip', skip_reason=skips[0]['reason'])
        return result
    if status != 'ok' or free < threshold or skips or phase('error') or len(phase('vm_activity')) != 1 or len(phase('vm_usage')) != 1 or len(phase('memory_check')) != 1:
        return result
    measurements = [r['fields'] for r in rows if r['fields'].get('verified') == '1']
    if len(measurements) != 1:
        return result
    m = measurements[0]
    try:
        start, end, elapsed, size, logical = (int(m[k]) for k in ('start_monotonic_ns', 'end_monotonic_ns', 'elapsed_ns', 'size_bytes', 'logical_bytes'))
    except (KeyError, ValueError):
        return result
    if size != 65536 or logical != size or end <= start or elapsed != end-start or m.get('threads') != '1' or m.get('synthetic') != '1':
        return result
    result.update(valid=True, outcome='completed', logical_bytes=logical, elapsed_ns=elapsed,
                  logical_mib_per_second=logical * 1e9 / elapsed / (1024*1024),
                  limitation='logical copy; cache and workload influence; not DRAM bandwidth')
    return result
