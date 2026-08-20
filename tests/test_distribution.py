from pathlib import Path
import subprocess
import unittest


ROOT = Path(__file__).resolve().parents[1]


class DistributionTests(unittest.TestCase):
    def test_shell_scripts_parse(self):
        for name in ("install.sh", "uninstall.sh"):
            completed = subprocess.run(
                ["bash", "-n", str(ROOT / name)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_manifest_paths_are_safe_and_present(self):
        manifest = ROOT / "install-manifest.txt"
        paths = []
        for line in manifest.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = Path(line)
            self.assertFalse(path.is_absolute())
            self.assertNotIn("..", path.parts)
            self.assertTrue((ROOT / path).is_file(), line)
            paths.append(line)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn("proxy.py", paths)
        self.assertIn("agents/codex.py", paths)


if __name__ == "__main__":
    unittest.main()
