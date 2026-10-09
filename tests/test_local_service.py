from __future__ import annotations

import plistlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts import local_service


class LocalServiceTests(unittest.TestCase):
    def test_launch_agent_runs_the_portal_on_loopback_and_restarts_it(self) -> None:
        root = Path('/tmp/vortex-portal')
        log_dir = Path('/tmp/vortex-logs')
        data = Path('/tmp/vortex-data/portal.db')
        config = local_service.make_plist(root, log_dir, data)

        self.assertEqual(config['Label'], 'com.vortexveri.portal')
        self.assertEqual(config['ProgramArguments'], ['/bin/bash', str(root.resolve() / 'run.sh')])
        self.assertEqual(config['WorkingDirectory'], str(root.resolve()))
        self.assertEqual(config['EnvironmentVariables']['PORTAL_HOST'], '127.0.0.1')
        self.assertEqual(config['EnvironmentVariables']['PYTHON'], '/usr/bin/python3')
        self.assertEqual(config['EnvironmentVariables']['PORTAL_DB_PATH'], str(data))
        self.assertTrue(config['RunAtLoad'])
        self.assertTrue(config['KeepAlive'])
        self.assertEqual(config['StandardOutPath'], str(log_dir / 'portal.log'))
        self.assertEqual(config['StandardErrorPath'], str(log_dir / 'portal.err.log'))

    def test_install_writes_private_log_directory_and_repeatable_plist(self) -> None:
        with TemporaryDirectory() as temp:
            root = Path(temp) / 'portal'
            root.mkdir()
            (root / 'run.sh').write_text('#!/bin/bash\n')
            (root / '.env').write_text('PORTAL_MCP_TOKEN=test-private\n')
            (root / 'portal').mkdir()
            (root / 'portal' / 'cli.py').write_text('pass\n')
            (root / 'tests').mkdir()
            (root / 'tests' / 'unneeded.py').write_text('pass\n')
            home = Path(temp) / 'home'
            first = local_service.install(root, home)
            initial = first.read_bytes()
            second = local_service.install(root, home)

            self.assertEqual(first, second)
            self.assertEqual(initial, second.read_bytes())
            runtime = home / 'Library/Application Support/VortexVeri/runtime'
            self.assertEqual(plistlib.loads(initial)['WorkingDirectory'], str(runtime.resolve()))
            self.assertEqual(plistlib.loads(initial)['EnvironmentVariables']['PORTAL_DB_PATH'],
                             str((home / 'Library/Application Support/VortexVeri/data/portal.db').resolve()))
            self.assertEqual((runtime / 'portal/cli.py').read_text(), 'pass\n')
            self.assertFalse((runtime / 'tests').exists())
            self.assertEqual((runtime / '.env').stat().st_mode & 0o777, 0o600)
            self.assertEqual(runtime.stat().st_mode & 0o777, 0o700)
            self.assertEqual((home / 'Library/Logs/VortexVeri').stat().st_mode & 0o777, 0o700)


if __name__ == '__main__':
    unittest.main()
