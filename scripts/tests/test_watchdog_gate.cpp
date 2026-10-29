#include "../../package/A04BR3WatchdogGate.h"
#include <atomic>
#include <cassert>
#include <thread>
int main() {
  A04BR3WatchdogGate gate = {};
  volatile uint32_t active=0;
  unsigned writes=0;
  assert(gate.feed(&active,true,[&]{++writes;}));
  assert(writes==1 && gate.begin(&active));
  assert(!gate.begin(&active));
  assert(!gate.feed(&active,true,[&]{++writes;}));
  assert(writes==1);
  __atomic_store_n(&active,0U,__ATOMIC_RELEASE);
  assert(!gate.feed(&active,false,[&]{++writes;}));
  std::atomic<bool> inside{false}, release{false};
  std::thread feeder([&]{assert(gate.feed(&active,true,[&]{inside.store(true);while(!release.load())std::this_thread::yield();++writes;}));});
  while(!inside.load()) std::this_thread::yield();
  assert(!gate.begin(&active)); // an in-flight MMIO kick must finish first
  assert(!__atomic_load_n(&active,__ATOMIC_ACQUIRE));
  release.store(true); feeder.join();
  assert(gate.begin(&active));
  assert(!gate.feed(&active,true,[&]{++writes;}));
  assert(writes==2 && __atomic_load_n(&active,__ATOMIC_ACQUIRE));
}
