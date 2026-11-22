"""Check for updates (engine/update.py) against a local HTTPS test server (test_setup.py's). Never shipped.
The check only says what is on the website: these tests also make sure nothing else can be asked of it.

Run:  python3 app/tests/test_update.py
"""
import json
import sys
import time
import tempfile
import unittest
from unittest import mock
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parents[1])]
import test_setup as T            # noqa: E402
from engine import update         # noqa: E402

ALLOWED = {'127.0.0.1'}


def wait(u, t=20):
    end = time.time() + t
    while time.time() < end:
        s = u.state()
        if s['state'] != 'checking':
            return s
        time.sleep(0.05)
    raise AssertionError(f'still {u.state()}')


class UpdateTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.files = T.Files()
        cls.srv, cls.ctx, cls.base = T.start_server(cls.tmp, cls.files)

    def meta(self, version='1.2.0', min_os='10.0', **extra):
        self.files.data['app-v1.json'] = json.dumps({'version': 1, **{k: dict(version=version, min_os=min_os, **extra) for k in ('mac', 'windows', 'linux')}}).encode()

    def check(self, version='1.1.2', url=None, key='mac', system='14.0'):
        """system: this computer's version as os_version() would say, so the result does not depend on the machine running the tests."""
        u = update.Updater(url or f'{self.base}/app-v1.json', allowed=ALLOWED, context=self.ctx, key=key, version=version)
        with mock.patch.object(update, 'os_version', return_value=system):
            u.check()
            return wait(u)

    def test_available(self):
        self.meta(url='https://evil.example/x.dmg', sha256='0' * 64, size=1)    # anything else in the file is not read
        s = self.check()
        self.assertEqual((s['state'], s['version'], s['current'], s['website']),
                         ('available', '1.2.0', '1.1.2', 'github.com/ignFT37/Lastbook-ES/releases'))    # Lastbook ES: its own releases
        self.assertEqual(set(s), {'state', 'version', 'current', 'website'})

    def test_up_to_date(self):
        self.meta(version='1.1.2')
        self.assertEqual(self.check()['state'], 'up_to_date')

    def test_offline(self):
        s = self.check(url='https://127.0.0.1:1/app-v1.json')     # an allowed address that nothing answers
        self.assertEqual(s['code'], 'offline')
        self.assertIn('still works', s['message'])

    def test_not_published(self):
        self.files.data.pop('app-v1.json', None)
        self.assertEqual(self.check()['state'], 'not_published')

    def test_bad_version(self):
        self.meta(version='<b>2</b>')
        self.assertEqual(self.check()['code'], 'bad_info')

    def test_too_old_os(self):
        self.meta(min_os='99.0')
        self.assertEqual(self.check()['state'], 'too_old_os')

    def test_unknown_system_is_never_too_old(self):
        self.meta(min_os='99.0')
        self.assertEqual(self.check(system=None)['state'], 'available')

    def test_linux_built_on_ubuntu(self):
        self.meta(min_os='24.04')
        self.assertEqual(self.check(key='linux', system='24.04')['state'], 'available')
        s = self.check(key='linux', system='22.04')
        self.assertEqual(s['state'], 'too_old_os')
        self.assertEqual(s['message'], 'Version 1.2.0 needs Ubuntu 24.04 or later, or a system built on it.')

    def test_linux_version(self):
        cases = [
            ({'ID': 'ubuntu', 'VERSION_ID': '24.04', 'UBUNTU_CODENAME': 'noble'}, '24.04'),
            ({'ID': 'ubuntu', 'VERSION_ID': '26.04', 'UBUNTU_CODENAME': 'resolute'}, '26.04'),
            ({'ID': 'linuxmint', 'ID_LIKE': 'ubuntu debian', 'VERSION_ID': '22.1', 'UBUNTU_CODENAME': 'noble'}, '24.04'),   # not 22.1
            ({'ID': 'elementary', 'ID_LIKE': 'ubuntu debian', 'VERSION_ID': '8', 'UBUNTU_CODENAME': 'noble'}, '24.04'),       # not 8
            ({'ID': 'zorin', 'ID_LIKE': 'ubuntu debian', 'VERSION_ID': '17', 'UBUNTU_CODENAME': 'jammy'}, '22.04'),
            ({'ID': 'pop', 'ID_LIKE': 'ubuntu debian', 'VERSION_ID': '24.04', 'UBUNTU_CODENAME': 'noble'}, '24.04'),
            ({'ID': 'linuxmint', 'VERSION_ID': '30', 'UBUNTU_CODENAME': 'someday'}, None),      # a code name this version does not know
            ({'ID': 'debian', 'VERSION_ID': '13'}, None),
            ({'ID': 'fedora', 'VERSION_ID': '43'}, None),
            ({'ID': 'arch', 'BUILD_ID': 'rolling'}, None),
            ({}, None),
        ]
        for release, want in cases:
            with self.subTest(release=release.get('ID')):
                self.assertEqual(update.linux_version(release), want)

    def test_windows_build_numbers(self):
        self.assertEqual(update._ver('10.0.26200'), (10, 0, 26200))     # five-digit Windows builds must compare
        self.assertGreater(update._ver('10.0.26200'), update._ver('10.0'))

    def test_product_reaches_only_this_forks_releases(self):
        """Lastbook ES: the fork's latest release on GitHub, never the official site, whose versions are the official app's."""
        u = update.Updater(key='mac', version='1.0.0')
        self.assertEqual((u.allowed, u.url), ({'github.com', 'release-assets.githubusercontent.com', 'objects.githubusercontent.com'},
                                              'https://github.com/ignFT37/Lastbook-ES/releases/latest/download/app-v1.json'))
        for other in ('https://downloads.lastbook.org/app-v1.json', 'https://huggingface.co/app-v1.json',
                      'https://github.com:8443/ignFT37/Lastbook-ES/releases/latest/download/app-v1.json'):
            u = update.Updater(key='mac', version='1.0.0')
            u.url = other
            u.check()
            s = wait(u)
            self.assertEqual(s['state'], 'error', other)
            self.assertNotEqual(s['state'], 'available')

    def test_only_check_and_state(self):
        self.assertEqual(set(update.ROUTES), {('GET', 'state'), ('POST', 'check')})
        for gone in ('download', 'install', 'stop', '_install_mac', '_install_windows'):
            self.assertFalse(hasattr(update.Updater, gone), gone)
        self.assertFalse(hasattr(update, 'QUIT'))
        self.assertNotIn('subprocess', vars(update))      # nothing to start: no program is run


if __name__ == '__main__':
    unittest.main(verbosity=2)
