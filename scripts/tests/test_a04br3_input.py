"""A04BR3Input の実際の publish 関数をホストで並行実行する回帰試験。"""
from pathlib import Path
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'package/A04BR3Input.cpp'


class InputPublishTest(unittest.TestCase):
    def test_two_writers_and_callback(self):
        source = SOURCE.read_text()
        publish = source[source.index('void publish(bool'):source.index('\nvoid touchThread(')]
        # ARM のメモリバリアだけをホストのバリアへ置換する。
        publish = publish.replace('__asm__ volatile("dmb ish" ::: "memory");',
                                  'std::atomic_thread_fence(std::memory_order_seq_cst);')
        harness = r'''
#include <atomic>
#include <cassert>
#include <cstdint>
#include <mutex>
#include <thread>
using IOLock = std::mutex;
IOLock lock;
IOLock *gPublishLock = &lock;
thread_local bool locked = false;
void IOLockLock(IOLock *p) { p->lock(); locked = true; }
void IOLockUnlock(IOLock *p) { locked = false; p->unlock(); }
volatile uint32_t native_touch_seq, native_touch_count;
volatile int native_touch_xs[12], native_touch_ys[12], native_touch_ids[12];
std::atomic<unsigned> notifications{0};
void native_touch_published() {
    assert(!locked); // work loop を呼ぶ際はロックを保持しない。
    ++notifications;
}
''' + publish + r'''
int main() {
    publish(true, 799, 1279);
    assert(native_touch_seq == 2 && native_touch_count == 1);
    assert(native_touch_ids[0] == 0);
    assert(native_touch_xs[0] == 30479 && native_touch_ys[0] == 20319);
    publish(false, 0, 0);
    assert(native_touch_seq == 4 && native_touch_count == 0);
    assert(native_touch_xs[0] == 0 && native_touch_ys[0] == 0);
    constexpr unsigned frames = 200000;
    auto panel = [] { for (unsigned i = 0; i < frames; ++i) publish(i & 1, 10, 20); };
    auto selftest = [] { for (unsigned i = 0; i < frames; ++i) publish(i & 1, 700, 640); };
    std::thread a(panel), b(selftest);
    a.join(); b.join();
    assert(native_touch_seq == 4 + 4 * frames);
    assert(!(native_touch_seq & 1));
    assert(notifications == 2 + 2 * frames);
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-input-test-') as tmp:
            cpp = Path(tmp) / 'publish.cpp'
            binary = Path(tmp) / 'publish'
            cpp.write_text(harness)
            subprocess.run(['c++', '-std=c++17', '-O2', '-pthread', '-Wall', '-Wextra',
                            '-Werror', str(cpp), '-o', str(binary)], check=True)
            subprocess.run([str(binary)], check=True)
            # 同じ試験が修正前の実装で失敗することも確認する。
            cpp.write_text(harness.replace('IOLockLock(gPublishLock);', '')
                           .replace('IOLockUnlock(gPublishLock);', ''))
            subprocess.run(['c++', '-std=c++17', '-O2', '-pthread', str(cpp),
                            '-o', str(binary)], check=True)
            result = subprocess.run([str(binary)], stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE)
            self.assertNotEqual(result.returncode, 0, '競合の再現に失敗')


if __name__ == '__main__':
    unittest.main()
