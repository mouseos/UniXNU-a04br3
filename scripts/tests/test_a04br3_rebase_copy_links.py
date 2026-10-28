#!/usr/bin/env python3
import importlib.util
from pathlib import Path
import tempfile
import unittest

script = Path(__file__).resolve().parents[1] / 'a04br3-rebase-copy-links.py'
spec = importlib.util.spec_from_file_location('copy_links', script)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


class RebaseCopyLinksTest(unittest.TestCase):
    def test_rebase_and_preflight_preserve_other_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); original = p / 'original'; copied = p / 'copy'
            for root in (original, copied):
                (root / 'out').mkdir(parents=True)
                (root / 'source').write_text('original content')
            link = copied / 'out' / 'generated'
            link.symlink_to(original / 'source')
            untouched = copied / 'out' / 'regular'
            untouched.write_text('do not change')
            self.assertEqual(mod.rebase(original, copied, False), ['out/generated'])
            self.assertEqual(link.resolve(), (original / 'source').resolve())
            self.assertEqual(mod.rebase(original, copied, True), ['out/generated'])
            self.assertEqual(link.resolve(), (copied / 'source').resolve())
            self.assertEqual(untouched.read_text(), 'do not change')
            self.assertEqual(mod.rebase(original, copied, True), [])

    def test_unsafe_links_abort_before_any_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp); original = p / 'original'; copied = p / 'copy'
            for root in (original, copied):
                (root / 'out').mkdir(parents=True)
                (root / 'source').write_text('x')
            good = copied / 'out' / 'good'; good.symlink_to(original / 'source')
            bad = copied / 'out' / 'bad'; bad.symlink_to(p / 'outside')
            with self.assertRaises(ValueError): mod.rebase(original, copied, True)
            self.assertEqual(good.resolve(), (original / 'source').resolve())
            bad.unlink(); bad = copied / 'bad'; bad.symlink_to(original / 'source')
            with self.assertRaises(ValueError): mod.rebase(original, copied, True)
            self.assertEqual(good.resolve(), (original / 'source').resolve())
            bad.unlink(); (copied / 'source').unlink()
            with self.assertRaises(ValueError): mod.rebase(original, copied, True)
            self.assertEqual(good.resolve(), (original / 'source').resolve())


if __name__ == '__main__':
    unittest.main()
