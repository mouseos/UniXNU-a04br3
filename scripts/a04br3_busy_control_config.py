"""Build opt-in for a fail-closed CPU3 control prototype."""

def busy_control_flags(env):
    value = env.get('XNU_ARMCPU_BUSY_CONTROL', '0')
    if value not in ('0', '1'):
        raise ValueError('XNU_ARMCPU_BUSY_CONTROL must be 0 or 1')
    if value == '0':
        return []
    required = ('XNU_ARMCPU_GICV2', 'XNU_ARMCPU_A04BR3_RUNTIME',
                'XNU_ARMCPU_RUNTIME_WINDOW', 'XNU_ARMCPU_IDLE_EPOCH', 'XNU_PMUV3_ACTIVE',
                'XNU_ARMCPU_SWITCH_OBSERVER')
    missing = [name for name in required if env.get(name) != '1']
    if missing:
        raise ValueError('XNU_ARMCPU_BUSY_CONTROL=1 requires ' + ', '.join(missing))
    return ['-DARMCPU_RUNTIME_BUSY_CONTROL=1']
