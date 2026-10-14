import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from agent import keyring


class KeyringTests(unittest.TestCase):
    def setUp(self):
        self.old_status, self.old_error = keyring.STATUS, keyring.ERROR
        self.old_bus = os.environ.get('DBUS_SESSION_BUS_ADDRESS')
        keyring.STATUS, keyring.ERROR = 'not_started', ''

    def tearDown(self):
        keyring.STATUS, keyring.ERROR = self.old_status, self.old_error
        if self.old_bus is not None:
            os.environ['DBUS_SESSION_BUS_ADDRESS'] = self.old_bus

    def test_missing_binary_nonfatal(self):
        with patch('agent.keyring.shutil.which', return_value=None), patch('builtins.print'):
            self.assertFalse(keyring.start_keyring())
            self.assertEqual(keyring.STATUS, 'unavailable')
            self.assertIn('custom Dockerfile', keyring.ERROR)

    def test_dbus_error_nonfatal_and_diagnosed(self):
        with patch('agent.keyring.shutil.which', return_value='/mock/bin'), \
             patch('agent.keyring.spawn_bus', side_effect=RuntimeError('Failed to bind socket: Permission denied')), \
             patch('builtins.print'):
            self.assertFalse(keyring.start_keyring())
            self.assertIn('Permission denied', keyring.ERROR)

    def test_success_and_private_password(self):
        with tempfile.TemporaryDirectory() as t, patch.dict(os.environ, {'HOME':t}), \
             patch('agent.keyring.shutil.which', return_value='/mock/bin'), \
             patch('agent.keyring.spawn_bus'), \
             patch('agent.keyring.subprocess.run', return_value=SimpleNamespace(returncode=0,stderr='')):
            self.assertTrue(keyring.start_keyring())
            self.assertEqual(keyring.STATUS, 'ready')
            self.assertEqual((Path(t)/'.keyring-password').stat().st_mode & 0o777, 0o600)

    def test_diagnostic_redacts_urls(self):
        result=keyring.diagnostic('Failed https://example.test/?token=SECRET_VALUE')
        self.assertNotIn('SECRET_VALUE', result)
