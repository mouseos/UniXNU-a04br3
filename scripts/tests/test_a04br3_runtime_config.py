from pathlib import Path
import importlib.util
import tempfile
import unittest

path = Path(__file__).resolve().parents[1] / 'a04br3_runtime_config.py'
spec = importlib.util.spec_from_file_location('a04br3_runtime_config', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class RuntimeConfigTest(unittest.TestCase):
    def test_requires_exact_generated_disabled_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'config_cpu_counters.h'
            with self.assertRaises(ValueError):
                module.require_cpu_counters_disabled(config)
            config.write_text('#define CONFIG_CPU_COUNTERS 1\n')
            with self.assertRaises(ValueError):
                module.require_cpu_counters_disabled(config)
            config.write_text('#define CONFIG_CPU_COUNTERS 0\n')
            module.require_cpu_counters_disabled(config)

if __name__ == '__main__':
    unittest.main()
