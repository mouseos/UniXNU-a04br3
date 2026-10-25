"""Run the actual UART parser against a host FIFO, with and without HID linked."""
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[2] / 'package/A04BR3AutoReset.cpp'


class UartParserTest(unittest.TestCase):
    def test_commands_and_bootstrap(self):
        source = SOURCE.read_text()
        parser = source[source.index('bool resetRequested(void)'):source.index('\nunsigned gResetSeconds;')]
        constants = '\n'.join(re.findall(r'constexpr char k\w+Request\[\] = .*?;', source))
        base = r'''
#include <cassert>
#include <cstdint>
#include <deque>
#include <string>
extern "C" volatile uint32_t native_hid_input_command __attribute__((weak));
std::deque<unsigned char> fifo;
volatile uint8_t dummy;
volatile uint8_t *gUart = &dummy;
unsigned gRequestMatched, gShotMatched, gHomeMatched;
bool gShotWanted;
constexpr unsigned kUartRbr = 0, kUartLsr = 0x14;
void IOLog(const char *, ...) {}
uint32_t rd(volatile uint8_t *, unsigned reg) {
    if (reg == kUartLsr) return !fifo.empty();
    assert(reg == kUartRbr && !fifo.empty());
    unsigned char c = fifo.front(); fifo.pop_front(); return c;
}
void feed(const std::string &s) { for (unsigned char c : s) fifo.push_back(c); }
''' + constants + '\n' + parser + r'''
int main() {
    feed("noise\nA04BR3!HO"); assert(!resetRequested());
#ifdef WITH_HID
    assert(native_hid_input_command == 0);
#endif
    feed("ME\n"); assert(!resetRequested());
#ifdef WITH_HID
    assert(native_hid_input_command == 1);
    native_hid_input_command = 0;
#endif
    feed("A04BR3!SHOT\nA04BR3!HOME\n"); assert(!resetRequested());
    assert(gShotWanted);
    feed("A04BR3!R"); assert(!resetRequested());
    feed("ST\n"); assert(resetRequested());
    gRequestMatched = 0; // production resets the device on a complete RST
    fifo.clear();
    feed(std::string(63, '?') + "A04BR3!HOME\n");
    assert(!resetRequested()); assert(!fifo.empty());
    assert(!resetRequested()); assert(fifo.empty());
    gUart = nullptr; feed("A04BR3!RST"); assert(!resetRequested());
}
'''
        with tempfile.TemporaryDirectory(prefix='a04br3-uart-test-') as tmp:
            cpp = Path(tmp) / 'parser.cpp'
            cpp.write_text(base)
            hid_cpp = Path(tmp) / 'hid.cpp'
            hid_cpp.write_text('#include <cstdint>\nextern "C" { volatile uint32_t native_hid_input_command; }\n')
            for hid in (False, True):
                binary = Path(tmp) / ('hid' if hid else 'bootstrap')
                args = ['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror']
                if hid:
                    args.extend(['-DWITH_HID', str(hid_cpp)])
                subprocess.run(args + [str(cpp), '-o', str(binary)], check=True)
                subprocess.run([str(binary)], check=True)


if __name__ == '__main__':
    unittest.main()
