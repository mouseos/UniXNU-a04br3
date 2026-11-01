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
        # The board's firmware define selects the payload FAT descriptor (uefi-a04br3 A04BR3_RAMDISK_RECLAIMABLE).
        cls.reclaimable = cls.board['firmware']['defines'].get('A04BR3_RAMDISK_RECLAIMABLE', '0') == '1'
        branch = re.search(r'#if A04BR3_RAMDISK_RECLAIMABLE\n(.*?)#else\n(.*?)#endif\n', source, re.S)
        if branch:
            source = source.replace(branch.group(0), branch.group(1 if cls.reclaimable else 2))
        elif cls.reclaimable:
            raise AssertionError('board.toml enables A04BR3_RAMDISK_RECLAIMABLE but the uefi-a04br3 checkout lacks it')
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
                         (0x60000000, 0x60000000, 0x08000000, 'BsData' if self.reclaimable else 'Reserv'))
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
        # The largest 2 MiB aligned request that fits with any kernel VA alignment offset (DarwinBoot AllocateWindow).
        self.assertEqual(request, 0x56600000)
        self.assertEqual(request % 0x200000, 0)
        self.assertLess(base + 0x1FFFFF, 0x80000000)
        self.assertLessEqual(base + 0x1FFFFF + request, base + size)
        self.assertGreater(base + 0x1FFFFF + request + 0x200000, base + size)
        lower_base, lower_size, lower_kind = self.regions['DDR Mid 2']
        self.assertEqual((lower_base, lower_size, lower_kind),
                         (0x52000000, 0x0E000000, 'Conv'))
        self.assertLess(lower_size, request)

    def test_memory_floor_spans_only_memory_the_os_may_take(self):
        # [payload.adt] memory_floor (make-a04br3-adt.py --memory-floor, DarwinBoot darwinboot-memory-floor): everything from
        # the floor up to the window must be conventional memory or the boot services data payload FAT, never a reservation.
        floor = self.board['payload']['adt'].get('memory_floor')
        if floor is None:
            self.skipTest('no memory floor in board.toml')
        floor = int(floor, 0)
        self.assertEqual(floor % 0x200000, 0)
        self.assertTrue(0x52000000 <= floor < 0x68000000)
        self.assertTrue(self.reclaimable, 'a floor below the payload FAT needs A04BR3_RAMDISK_RECLAIMABLE = "1"')
        mid2, fat, high = self.regions['DDR Mid 2'], self.regions['Boot Ramdisk'], self.regions['DDR High']
        self.assertEqual((mid2[0], mid2[0] + mid2[1], mid2[2]), (0x52000000, fat[0], 'Conv'))
        self.assertEqual((fat[0] + fat[1], fat[2]), (high[0], 'BsData'))
        self.assertLessEqual(mid2[0], floor)
        heap = self.regions['DXE Heap']
        self.assertEqual(heap[0] + heap[1], 0x52000000)   # the DXE heap (UEFI runtime) ends where the lowest floor starts
        for name in ('ATF Reserved', 'RAM Console', 'PStore', 'MiniRdDump', 'LK DTB', 'UEFI FD', 'UEFI Stack'):
            base, size, _ = self.regions[name]
            self.assertLessEqual(base + size, floor, name)

    def test_memory_floor_reaches_the_kernel_static_region_limit(self):
        # DarwinBoot hands XNU [floor, window) only if VirtualBase = kernel VA - (window PA - floor) stays at or above the
        # static region floor: the low globals plus their 8 MiB reservation (pmap_virtual_region); otherwise it raises the
        # floor. a04br3_defconfig moves the low globals (CONFIG_XNU_LOW_GLOBALS_BELOW, UniXNU
        # patches/xnu/zzzzz-low-globals-below.patch), which DarwinBoot reads from __DATA_CONST,__lowglo_base.
        floor = self.board['payload']['adt'].get('memory_floor')
        if floor is None:
            self.skipTest('no memory floor in board.toml')
        floor = int(floor, 0)
        kernel_va = 0xFFFFFFF00427C000        # lowest kernel segment (__PRELINK_TEXT, [kernel.prelink] slide), UART
        default_limit = 0xFFFFFFF000000000 + 0x800000
        moved_limit = 0xFFFFFFEFE0000000 + 0x800000
        window_pa = self.regions['DDR High'][0] + 0x1FFFFF   # the largest VA alignment offset AllocateWindow adds
        below = window_pa - floor
        self.assertGreater(below, kernel_va - default_limit)   # the stock low globals would raise the floor
        self.assertLessEqual(below, kernel_va - moved_limit)  # the moved ones admit it, with any offset
        # The official defconfig; a diagnostic one without the option still boots (DarwinBoot raises the floor).
        config = (DEVICE / 'configs' / 'a04br3_defconfig').read_text().splitlines()
        self.assertIn('CONFIG_XNU_LOW_GLOBALS_BELOW=y', config)


if __name__ == '__main__':
    unittest.main()
