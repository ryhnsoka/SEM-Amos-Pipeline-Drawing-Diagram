"""Regression tests for non-destructive plugin deployment and -WhatIf / -PruneOldPlugins flags."""
from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUILTIN_PLUGINS = [
    "DrawCovariances.dll",
    "GrowthCurveModel.dll",
    "NameParameters.dll",
    "NameUnobservedVariables.dll",
    "ResizeObservedVariables.dll",
    "StandardizedRMR.dll",
]


def _has_amos_toolchain() -> bool:
    vbc = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Microsoft.NET" / "Framework" / "v4.0.30319" / "vbc.exe"
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    amos_dll = Path(pf86) / "IBM" / "SPSS" / "Amos" / "23" / "AmosGraphics.dll"
    return vbc.exists() and amos_dll.exists()


@unittest.skipUnless(_has_amos_toolchain(), "Requires local Windows .NET 4.0 x86 vbc.exe and IBM SPSS Amos 23")
class TestPluginDeployment(unittest.TestCase):
    def _run_deploy(self, plugins_dir: Path, extra_args: list[str]) -> subprocess.CompletedProcess[str]:
        cmd = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(ROOT / "scripts" / "amos_pipeline.ps1"),
            "-SpecFile",
            "spec\\template_model.txt",
            "-SkipFit",
            "-Deploy",
            "-PluginsDirOverride",
            str(plugins_dir),
            *extra_args,
        ]
        return subprocess.run(cmd, cwd=str(ROOT), capture_output=True, text=True)

    def _seed_plugins(self, plugins_dir: Path) -> None:
        for name in BUILTIN_PLUGINS:
            (plugins_dir / name).write_bytes(b"builtin")
        (plugins_dir / "UnrelatedCustomPlugin.dll").write_bytes(b"custom_keep")
        (plugins_dir / "TextDemoSEM.dll").write_bytes(b"old_target_version")

    def test_default_deploy_preserves_unrelated_and_builtin_plugins_and_backs_up_target(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            self._seed_plugins(pdir)
            proc = self._run_deploy(pdir, [])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertTrue((pdir / "TextDemoSEM.dll").exists())
            self.assertGreater((pdir / "TextDemoSEM.dll").stat().st_size, 100)
            self.assertTrue((pdir / "UnrelatedCustomPlugin.dll").exists())
            for name in BUILTIN_PLUGINS:
                self.assertTrue((pdir / name).exists())

    def test_whatif_does_not_modify_plugins_dir(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            self._seed_plugins(pdir)
            proc = self._run_deploy(pdir, ["-PruneOldPlugins", "-WhatIf"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual((pdir / "TextDemoSEM.dll").read_bytes(), b"old_target_version")
            self.assertTrue((pdir / "UnrelatedCustomPlugin.dll").exists())

    def test_prune_old_plugins_removes_custom_only_when_requested_and_respects_keep(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            pdir = Path(td)
            self._seed_plugins(pdir)
            # KeepOldPlugins overrides PruneOldPlugins
            proc_keep = self._run_deploy(pdir, ["-PruneOldPlugins", "-KeepOldPlugins"])
            self.assertEqual(proc_keep.returncode, 0, proc_keep.stdout + proc_keep.stderr)
            self.assertTrue((pdir / "UnrelatedCustomPlugin.dll").exists())

            # PruneOldPlugins removes UnrelatedCustomPlugin.dll but NEVER built-in plugins
            proc_prune = self._run_deploy(pdir, ["-PruneOldPlugins"])
            self.assertEqual(proc_prune.returncode, 0, proc_prune.stdout + proc_prune.stderr)
            self.assertFalse((pdir / "UnrelatedCustomPlugin.dll").exists())
            self.assertTrue((pdir / "TextDemoSEM.dll").exists())
            for name in BUILTIN_PLUGINS:
                self.assertTrue((pdir / name).exists())


if __name__ == "__main__":
    unittest.main()
