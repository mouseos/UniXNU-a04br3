"""Cross-repository checks for the device's boot FAT and owned-RAM layout.

Run with the matching uefi-a04br3 checkout next to this device repository, or
set A04BR3_UEFI. These checks do not establish dynamic EFI allocation success.
"""
import os
from pathlib import Path
import re
import tomllib
import unittest

DEVICE = Path(__file__).resolve().parents[2]
UEFI = Path(os.environ.get('A04BR3_UEFI', DEVICE.parent / 'uefi-a04br3'))


class RamLayoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.config = tomllib.loads((UEFI / 'Resources/Configs/a04br3.toml').read_text())
        cls.board = tomllib.loads((DEVICE / 'board.toml').read_text())
        source = (UEFI / 'Platforms/Benesse/a04br3Pkg/Library/MemoryMapLib/MemoryMapLib.c').read_text()
        # Default build: drop the opt-in A04BR3_RAMDISK_RECLAIMABLE branch, keep its #else lines.
        cls.reclaimable = re.search(r'#if A04BR3_RAMDISK_RECLAIMABLE\n(.*?)#else\n', source, re.S)
        source = re.sub(r'#if A04BR3_RAMDISK_RECLAIMABLE\n.*?#else\n(.*?)#endif\n', r'\1', source, flags=re.S)
        # Read the actual DDR descriptors, excluding MMIO descriptors.
        cls.regions = {}
        for name, base, size, kind in re.findall(
                r'\{"([^"]+)",\s*(0x[0-9A-Fa-f]+),\s*(0x[0-9A-Fa-f]+),'
                r'\s*AddMem,\s*\w+,\s*SYS_MEM_CAP,\s*(\w+),', source):
            cls.regions[name] = (int(base, 16), int(size, 16), kind)

    def test_fat_header_matches_reserved_region(self):
        image = self.config['boot_image']
        base = image['base'] + image['ramdisk_offset']
        reserved_base, size, kind = self.regions['Boot Ramdisk']
        self.assertEqual((base, reserved_base, size, kind),
                         (0x60000000, 0x60000000, 0x08000000, 'Reserv'))
        self.assertLessEqual(self.board['payload']['size_mib'] << 20, size)

    def test_no_static_overlap_or_heap_shrink(self):
        regions = sorted((base, base + size, name)
                         for name, (base, size, _) in self.regions.items())
        self.assertGreater(len(regions), 10)
        for left, right in zip(regions, regions[1:]):
            self.assertLessEqual(left[1], right[0], (left, right))
        self.assertEqual(self.regions['DXE Heap'], (0x4E240000, 0x03DC0000, 'Conv'))
        self.assertEqual(self.regions['LK DTB'], (0x4E000000, 0x00200000, 'Reserv'))
        self.assertEqual(self.regions['ATF Reserved'], (0x43000000, 0x30000, 'Reserv'))
        self.assertEqual(self.regions['Display Reserved'], (0xBE9C0000, 0x1000000, 'Reserv'))
        self.assertEqual(self.regions['MBlock Gap'], (0xBF9C0000, 0x640000, 'Reserv'))

    def test_owned_window_fits_even_with_largest_alignment_offset(self):
        base, size, kind = self.regions['DDR High']
        request = int(self.board['payload']['adt']['owned_memory_size'], 0)
        self.assertEqual((base, size, kind), (0x68000000, 0x569C0000, 'Conv'))
        self.assertEqual(request, 0x54000000)
        self.assertEqual(request % 0x200000, 0)
        self.assertLess(base + 0x1FFFFF, 0x80000000)
        self.assertLessEqual(base + 0x1FFFFF + request, base + size)
        lower_base, lower_size, lower_kind = self.regions['DDR Mid 2']
        self.assertEqual((lower_base, lower_size, lower_kind),
                         (0x52000000, 0x0E000000, 'Conv'))
        self.assertLess(lower_size, request)

    def test_opt_in_window_override_stays_in_ddr_high(self):
        # make-a04br3-adt.py A04BR3_OWNED_MEMORY_SIZE: the largest accepted value with an offset up to 0x1C0000.
        base, size, _ = self.regions['DDR High']
        display, _, _ = self.regions['Display Reserved']
        self.assertEqual(base + size, display)
        largest = 0x56800000
        self.assertLessEqual(base + 0x1C0000 + largest, display)
        self.assertGreater(base + 0x1C0000 + largest + 0x200000, display)
        self.assertLessEqual(base + 0x7C000 + largest, display)  # current kernels: VA low bits 0x7C000


    def test_opt_in_floor_spans_only_reclaimable_memory(self):
        # make-a04br3-adt.py A04BR3_MEMORY_FLOOR: lowest accepted floor 0x52000000. Everything from there up to the
        # window must be conventional memory or, with A04BR3_RAMDISK_RECLAIMABLE=1, the boot services data payload FAT.
        if self.reclaimable is None:
            self.skipTest('uefi-a04br3 checkout without A04BR3_RAMDISK_RECLAIMABLE')
        floor = 0x52000000
        mid2 = self.regions['DDR Mid 2']
        fat = self.regions['Boot Ramdisk']
        high = self.regions['DDR High']
        self.assertEqual((mid2[0], mid2[0] + mid2[1]), (floor, fat[0]))
        self.assertEqual(fat[0] + fat[1], high[0])
        self.assertEqual(mid2[2], 'Conv')
        self.assertRegex(self.reclaimable.group(1), r'"Boot Ramdisk",\s*0x60000000,\s*0x08000000,\s*AddMem,\s*SYS_MEM,\s*SYS_MEM_CAP,\s*BsData,')
        heap = self.regions['DXE Heap']
        self.assertEqual(heap[0] + heap[1], floor)   # the DXE heap (UEFI runtime) ends where the floor may start
        for name in ('ATF Reserved', 'RAM Console', 'PStore', 'MiniRdDump', 'LK DTB', 'UEFI FD'):
            base, size, _ = self.regions[name]
            self.assertLessEqual(base + size, floor, name)

if __name__ == '__main__':
    unittest.main()
