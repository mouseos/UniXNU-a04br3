import importlib.util
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('check', Path(__file__).resolve().parents[1] / 'validate-a04br3-kernel.py')
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


class Check(unittest.TestCase):
    def test_symbols_must_be_defined(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'kernel'
            path.touch()
            good = '\n'.join(f'1234 T {symbol}' for symbol in check.REQUIRED)
            with patch.object(check.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=good)):
                check.validate(path, 'llvm-nm')
            missing = good.replace(' T __GLOBAL__sub_I_ShengPlatformPower.cpp', ' U __GLOBAL__sub_I_ShengPlatformPower.cpp')
            with patch.object(check.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=missing)):
                with self.assertRaises(ValueError): check.validate(path, 'llvm-nm')
            with patch.object(check.subprocess, 'run', return_value=SimpleNamespace(returncode=1, stdout='')):
                with self.assertRaises(ValueError): check.validate(path, 'llvm-nm')
            with self.assertRaises(ValueError): check.validate(path / 'missing', 'llvm-nm')


if __name__ == '__main__': unittest.main()
