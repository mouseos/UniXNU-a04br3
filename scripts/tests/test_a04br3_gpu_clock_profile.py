"""同じ描画条件を維持するGPUクロック比較profileの試験。"""
import importlib.util
from pathlib import Path
import unittest

script=Path(__file__).resolve().parents[1]/'a04br3-gpu-clock-profile.py'
spec=importlib.util.spec_from_file_location('profile',script)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
base='-v offline-legacy-cpu-present=1 a04br3-gpu=2 a04br3-gpu-metal=1 sheng-gpu-addon=1 sheng-fb-gpu=1'

class ProfileTests(unittest.TestCase):
    def test_variants_preserve_all_other_arguments(self):
        a=module.profile(base,26);b=module.profile(base,380)
        self.assertEqual(a['kernel_args'].split()[:-1],base.split())
        self.assertEqual(b['kernel_args'].split()[:-1],base.split())
        self.assertEqual(a['extra_kernel_arg'],'a04br3-gpu-clock=0')
        self.assertEqual(b['extra_kernel_arg'],'a04br3-gpu-clock=380')
        self.assertEqual(module.profile(b['kernel_args'],26),a)

    def test_no_cpu_fallback_regression_or_ambiguous_args(self):
        for bad in (base.replace('offline-legacy-cpu-present=1','offline-legacy-cpu-present=0'),
                    base.replace('a04br3-gpu-metal=1','a04br3-gpu-metal=0'),
                    base+' a04br3-gpu-clock=0 a04br3-gpu-clock=380', base+' injection;bad',
                    base+' padding='+('x'*1024)):
            with self.assertRaises(ValueError): module.profile(bad,380)
        with self.assertRaises(ValueError): module.profile(base,100)

if __name__=='__main__': unittest.main()
