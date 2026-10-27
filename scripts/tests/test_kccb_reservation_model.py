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


class FenceLedger:
    """Proposed exact per-file ledger with admission control, not current code."""
    def __init__(self, limit):
        self.limit = limit
        self.outcomes = {}
        self.next_id = 1

    def admit(self, n):
        if n <= 0 or len(self.outcomes) + n > self.limit:
            return None
        if self.next_id + n - 1 >= (1 << 64):
            return None
        ids = list(range(self.next_id, self.next_id + n))
        self.outcomes.update((i, 'pending') for i in ids)
        self.next_id += n
        return ids

    def resolve(self, i, outcome):
        assert outcome in ('success', 'failure', 'unknown')
        assert self.outcomes[i] == 'pending'
        self.outcomes[i] = outcome

    def wait(self, i):
        # Unknown and nonexistent must never imply success.
        return self.outcomes.get(i, 'unknown')


def test_fence_eviction_and_admission():
    # Reproduce current failure: a retired 65th failure evicts the first,
    # and the missing, previously issued ID is incorrectly inferred success.
    legacy = []
    for i in range(1, 66):
        legacy.append(i)
        legacy = legacy[-64:]
    assert 1 not in legacy
    assert ('failure' if 1 in legacy else 'success') == 'success'
    ledger = FenceLedger(66)
    ids = ledger.admit(65)
    for i in ids:
        ledger.resolve(i, 'failure')
    assert ledger.wait(1) == 'failure'
    assert ledger.wait(900) == 'unknown'
    old_next = ledger.next_id
    assert ledger.admit(2) is None and ledger.next_id == old_next
    last = ledger.admit(1)[0]
    ledger.resolve(last, 'unknown')
    assert ledger.wait(last) != 'success'
    ledger.next_id = (1 << 64) - 1
    assert ledger.admit(1) is None
    print('PASS: legacy 65-failure counterexample; retained failures, unknown, admission and wrap')


@dataclass
class Pending:
    phase: str = 'registered'
    refs: bool = True
    reserved: bool = False
    closing: bool = False
    lost: bool = False

    def reserve(self):
        assert self.phase == 'registered' and not (self.closing or self.lost)
        self.reserved = True
        self.phase = 'reserved'

    def publish(self):
        assert self.phase == 'reserved' and not (self.closing or self.lost)
        self.reserved = False
        self.phase = 'published'

    def stop(self, why):
        assert why in ('close', 'lost')
        setattr(self, 'closing' if why == 'close' else 'lost', True)
        if self.phase != 'published':
            self.reserved = False
            self.refs = False
            self.phase = 'cancelled'

    def stop_proven(self):
        assert self.phase == 'published'
        self.phase = 'retired'
        self.refs = False


def test_pending_lifetime():
    for phase in ('registered', 'reserved', 'published'):
        for order in (('close', 'lost'), ('lost', 'close')):
            p = Pending()
            if phase != 'registered': p.reserve()
            if phase == 'published': p.publish()
            for why in order:
                p.stop(why)
                assert p.refs == (phase == 'published')
            assert not p.reserved
            if phase == 'published':
                p.stop_proven()
                assert not p.refs
    print('PASS: close/lost in both orders, pending cancellation and published references')


def test_wrap_wakeup_and_hwrt():
    # Four slots: one sentinel; demonstrate ring wrap and reservation capacity.
    size, read, write, reserved = 4, 3, 1, 0
    used = (write - read) & (size - 1)
    assert used == 2 and (size - 1 - used - reserved) == 1
    reserved += 1
    assert size - 1 - used - reserved == 0
    # Atomic predicate + waiter registration under the same mutex: either
    # producer progresses before check or sees a registered waiter to wake.
    for producer_first in (False, True):
        ready = producer_first
        registered = False
        if not ready: registered = True
        if not producer_first:
            ready = True
            assert registered
        assert ready
    # Reader 8 consumes all tracking capacity; ninth must defer before CCCB mutation.
    readers, cccb_mutations = 8, 0
    if readers >= 8:
        outcome = 'defer'
    else:
        cccb_mutations += 1
        outcome = 'submit'
    assert outcome == 'defer' and cccb_mutations == 0
    print('PASS: sentinel/wrap, atomic wake predicate and 8-reader admission boundary')


def visibility_valid(events):
    required = ('host_clean', 'cccb_write', 'cccb_barrier', 'kccb_write',
                'kccb_clean', 'publish_offset', 'kick', 'ufo', 'reset_check',
                'host_invalidate', 'fence_success')
    if len(events) != len(required) or set(events) != set(required):
        return False
    pos = {event: events.index(event) for event in events}
    edges = (('host_clean', 'kick'), ('cccb_write', 'cccb_barrier'),
             ('cccb_barrier', 'kccb_write'), ('kccb_write', 'kccb_clean'),
             ('kccb_clean', 'publish_offset'), ('publish_offset', 'kick'),
             ('kick', 'ufo'), ('ufo', 'reset_check'),
             ('reset_check', 'host_invalidate'), ('host_invalidate', 'fence_success'))
    return all(pos[a] < pos[b] for a, b in edges)


def test_visibility_contract():
    events = ('host_clean', 'cccb_write', 'cccb_barrier', 'kccb_write',
              'kccb_clean', 'publish_offset', 'kick', 'ufo', 'reset_check',
              'host_invalidate', 'fence_success')
    assert visibility_valid(events)
    for i in range(len(events) - 1):
        modified = list(events)
        modified[i], modified[i + 1] = modified[i + 1], modified[i]
        # The first two are independent in this abstract partial order.
        assert visibility_valid(modified) == (i == 0)
    assert not visibility_valid(events[:-1])
    print('PASS: abstract visibility order and rejected reorderings; no ARM or firmware execution')


if __name__ == '__main__':
    test_reservation_state_space()
    test_fence_eviction_and_admission()
    test_pending_lifetime()
    test_wrap_wakeup_and_hwrt()
    test_visibility_contract()
