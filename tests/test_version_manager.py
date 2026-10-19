import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from core.version_manager import VersionManager
from core.files import operation_lock, sha256
from tests.fixtures import package


class VersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = package(self.root / 'source')
        self.vm = VersionManager(self.root / 'versions')

    def archive(self, prefix=''):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            for p in self.source.iterdir():
                archive.writestr(prefix + p.name, p.read_bytes())
        return buffer.getvalue()

    def release(self, payload):
        return {'tag_name': 'v0.9.4', 'assets': [{'name': 'opti.zip', 'size': len(payload),
                'digest': 'sha256:' + hashlib.sha256(payload).hexdigest(),
                'download_url': 'https://github.com/optiscaler/OptiScaler/releases/download/v0.9.4/opti.zip'}]}

    def install(self, payload, release=None):
        with patch('core.version_manager.urllib.request.urlopen', return_value=io.BytesIO(payload)):
            return self.vm.download_and_install_version(release or self.release(payload))

    def test_zip_nested_install_and_integrity(self):
        data = self.archive('package/')
        result = self.install(data)
        self.assertTrue(result['success'], result)
        self.assertTrue(self.vm.is_version_valid('v0.9.4'))
        (self.root / 'versions/v0.9.4/libxess.dll').write_bytes(b'tampered')
        self.assertFalse(self.vm.is_version_valid('v0.9.4'))

    def test_failed_redownload_preserves_previous(self):
        good = self.archive()
        self.assertTrue(self.install(good)['success'])
        original = sha256(self.root / 'versions/v0.9.4/OptiScaler.dll')
        release = self.release(good)
        self.assertFalse(self.install(good[:-5], release)['success'])
        self.assertTrue(self.vm.is_version_valid('v0.9.4'))
        self.assertEqual(sha256(self.root / 'versions/v0.9.4/OptiScaler.dll'), original)

    def test_digest_mismatch(self):
        data = self.archive()
        release = self.release(data)
        release['assets'][0]['digest'] = 'sha256:' + '0' * 64
        self.assertFalse(self.install(data, release)['success'])
        self.assertFalse((self.root / 'versions/v0.9.4').exists())

    def test_untrusted_url_and_tag_rejected(self):
        data = self.archive()
        release = self.release(data)
        release['assets'][0]['download_url'] = 'https://example.com/file.zip'
        self.assertFalse(self.install(data, release)['success'])
        release = self.release(data)
        release['tag_name'] = '../escape'
        self.assertFalse(self.install(data, release)['success'])

    def test_archive_traversal_rejected(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('../escape.dll', b'evil')
        self.assertFalse(self.install(buffer.getvalue())['success'])
        self.assertFalse((self.root / 'escape.dll').exists())

    def test_7z_extraction(self):
        import py7zr
        archive = self.root / 'package.7z'
        with py7zr.SevenZipFile(archive, 'w') as stream:
            stream.writeall(self.source, arcname='package')
        payload = archive.read_bytes()
        release = self.release(payload)
        release['assets'][0]['name'] = 'package.7z'
        self.assertTrue(self.install(payload, release)['success'])

    def test_concurrent_download_rejected(self):
        data = self.archive()
        with operation_lock(self.vm.versions_dir):
            self.assertFalse(self.install(data)['success'])

    def test_semantic_sort(self):
        tags = ['v0.9.9', 'v0.9.10', 'v0.10.0', 'v0.10.0-rc1']
        self.assertEqual(sorted(tags, key=self.vm.sort_key, reverse=True),
                         ['v0.10.0', 'v0.10.0-rc1', 'v0.9.10', 'v0.9.9'])

    def test_offline_does_not_invent_releases(self):
        with patch('core.version_manager.urllib.request.urlopen', side_effect=OSError('offline')):
            self.assertEqual(self.vm.fetch_available_releases(True), [])
        self.assertIn('offline', self.vm.last_error)

    def test_interrupted_commit_recovers_previous(self):
        data = self.archive()
        self.assertTrue(self.install(data)['success'])
        dest = self.root / 'versions/v0.9.4'
        dest.rename(self.root / 'versions/.previous-v0.9.4')
        with operation_lock(self.vm.versions_dir):
            self.vm._recover_commit('v0.9.4')
        self.assertTrue(self.vm.is_version_valid('v0.9.4'))

    def test_cancel_before_commit_retains_existing_release(self):
        import threading
        payload=self.archive();self.assertTrue(self.install(payload)['success'])
        before=sha256(self.root/'versions/v0.9.4/OptiScaler.dll')
        cancel=threading.Event()
        def progress(value,text):
            if value>=.8:cancel.set()
        with patch('core.version_manager.urllib.request.urlopen',return_value=io.BytesIO(payload)):
            result=self.vm.download_and_install_version(self.release(payload),progress,cancel)
        self.assertFalse(result['success']);self.assertIn('cancelled',result['error'])
        self.assertEqual(sha256(self.root/'versions/v0.9.4/OptiScaler.dll'),before)
