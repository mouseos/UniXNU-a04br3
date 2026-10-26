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


if __name__ == '__main__':
    unittest.main()
