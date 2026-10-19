"""Run isolated regression tests; never mutate the installed reference package."""
import hashlib
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / '.audit-deps')]


if __name__ == '__main__':
    before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'assets').rglob('*') if p.is_file()}
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), top_level_dir=str(ROOT))
    with (ROOT / 'audit/regression-results.txt').open('w', encoding='utf-8') as stream:
        result = unittest.TextTestRunner(stream=stream, verbosity=2).run(suite)
    after = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT / 'assets').rglob('*') if p.is_file()}
    assert before == after, 'Reference assets changed'
    print((ROOT / 'audit/regression-results.txt').read_text(encoding='utf-8'))
    sys.exit(not result.wasSuccessful())
