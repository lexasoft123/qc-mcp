"""Compile and run the real bridge callback code with offline IOKit recorders."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest


@unittest.skipUnless(sys.platform == "darwin" and shutil.which("clang"), "requires macOS SDK")
class InterposeTests(unittest.TestCase):
    def test_callback_registration_and_message_boundaries(self):
        source = pathlib.Path(__file__).with_name("interpose_harness.c")
        with tempfile.TemporaryDirectory() as directory:
            binary = pathlib.Path(directory) / "interpose-tests"
            subprocess.run(["clang", "-O1", "-g", "-fsanitize=address,undefined",
                            "-framework", "IOKit", "-framework", "CoreFoundation",
                            str(source), "-o", str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
