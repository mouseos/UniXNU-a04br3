#!/usr/bin/env python3
"""Exhaustively check a small reservation model, not the device driver or firmware."""
from collections import deque
from dataclasses import dataclass

@dataclass(frozen=True)
class State:
    used: int = 0
    batch: int = 0
    control: int = 0
    published: int = 0
    failed: bool = False


def successors(s, capacity=3):
    # Capacity excludes the sentinel. Batch and control both reserve shared slots.
    free = capacity - s.used - s.batch - s.control
    out = []
    if free >= 2 and not s.failed and s.batch == 0:
        out.append(State(s.used, 2, s.control, s.published, s.failed))
    if free and not s.failed and s.control == 0:
        out.append(State(s.used, s.batch, 1, s.published, s.failed))
    if s.batch and not s.failed:
        out.append(State(s.used + 1, s.batch - 1, s.control, s.published + 1, False))
    if s.control and not s.failed:
        out.append(State(s.used + 1, s.batch, 0, s.published, False))
    if s.used:
        out.append(State(s.used - 1, s.batch, s.control, s.published, s.failed))
    if s.batch or s.control:
        out.append(State(s.used, 0, 0, s.published, s.failed))
    if not s.failed:
        out.append(State(s.used, s.batch, s.control, s.published, True))
    return out


def test_reservation_state_space():
    # Bounded sequence count avoids an unbounded published counter; actual FW semantics are out of scope.
    q = deque([(State(), 0)])
    seen = {State()}
    while q:
        s, depth = q.popleft()
        assert 0 <= s.used + s.batch + s.control <= 3
        assert min(s.used, s.batch, s.control, s.published) >= 0
        if depth == 12:
            continue
        for n in successors(s):
            # Cancel and device loss must never erase already published work.
            assert n.published >= s.published
            if n not in seen:
                seen.add(n)
                q.append((n, depth + 1))
    assert any(s.failed and s.published and s.batch for s in seen)
    assert any(s.control and s.batch for s in seen)
    print(f'PASS: {len(seen)} abstract states; shared capacity, cancel, partial publish, device loss')

if __name__ == '__main__':
    test_reservation_state_space()
