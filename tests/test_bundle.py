import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile
from core.bundle import pack, unpack
from core.paths import prepare_assets
from tests.fixtures import package


class BundleTests(unittest.TestCase):
    def test_roundtrip_deduplicates_and_preserves_every_file(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); source = root / 'source'; source.mkdir()
            (source / 'a.dll').write_bytes(b'identical' * 100)
            (source / 'b.dll').write_bytes((source / 'a.dll').read_bytes())
            (source / 'license.txt').write_text('license')
            pack(source, root / 'bundle.zip')
            with zipfile.ZipFile(root / 'bundle.zip') as archive:
                self.assertEqual(len(archive.namelist()), 3)
            unpack(root / 'bundle.zip', root / 'out')
            for path in source.iterdir():
                self.assertEqual(path.read_bytes(), (root / 'out' / path.name).read_bytes())

    def test_rejects_escape_and_corrupt_hash(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for name in ('../escape', 'runtime.dll'):
                with zipfile.ZipFile(root / 'bundle.zip', 'w') as archive:
                    archive.writestr('manifest.json', json.dumps({name: {'sha256': '0'*64, 'size': 3}}))
                    archive.writestr('blobs/'+'0'*64, b'bad')
                with self.assertRaises(ValueError):
                    unpack(root / 'bundle.zip', root / 'out')
            self.assertFalse((root / 'escape').exists())

    def test_offline_seed_recovers_staging_and_preserves_existing_release(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); package(root / 'original')
            assets = root / 'resources/assets'; assets.mkdir(parents=True)
            pack(root / 'original/versions/v0.9.4', assets / 'offline-runtime.zip')
            storage = root / 'data'; stage = storage / 'assets/versions/.seed-v0.9.4'
            stage.mkdir(parents=True); (stage / 'partial').write_text('interrupted')
            with patch('core.paths.data_root', return_value=storage), patch('core.paths.resource_root', return_value=root / 'resources'):
                result = prepare_assets()
                target = result / 'versions/v0.9.4/OptiScaler.dll'
                self.assertTrue(target.exists())
                target.write_bytes(b'preserve existing installation')
                prepare_assets()
                self.assertEqual(target.read_bytes(), b'preserve existing installation')
                self.assertFalse(stage.exists())
