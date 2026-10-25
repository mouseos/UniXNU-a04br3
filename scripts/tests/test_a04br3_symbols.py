"""Exercise the real nm parser on LLVM's annotated and plain Mach-O symbols."""
import ast
from pathlib import Path
import re
import unittest


class SymbolsTest(unittest.TestCase):
    def test_annotations_do_not_become_names(self):
        path = Path(__file__).resolve().parents[1] / 'build-a04br3-legacy-display.py'
        node = next(n for n in ast.parse(path.read_text()).body if isinstance(n, ast.FunctionDef) and n.name == 'symbols')
        sample = '''archive.a(member.o):
fffffff006aeb688 (absolute) external [no dead strip] __ZN15IOAcceleratorES10gMetaClassE
0000000000000000 (__TEXT,__text) external [cold func] _cold
0000000000000010 (__TEXT,__text) external _plain
0000000000000020 (__TEXT,__text) weak external [no dead strip] _weak
                 (undefined) external _missing
                 (undefined) weak external _weak_missing
0000000000000030 (__TEXT,__text) non-external [no dead strip] _private
'''
        env = {'re': re, 'llvm': Path('/unused'), 'run': lambda *a, **kw: sample}
        exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), env)
        defined, weak, missing, weak_missing = env['symbols']('fixture.a')
        self.assertEqual(defined, {'__ZN15IOAcceleratorES10gMetaClassE', '_cold', '_plain'})
        self.assertEqual(weak, {'_weak'})
        self.assertEqual(missing, {'_missing'})
        self.assertEqual(weak_missing, {'_weak_missing'})


if __name__ == '__main__':
    unittest.main()
