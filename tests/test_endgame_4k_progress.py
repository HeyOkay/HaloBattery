"""A failed diagnostic side channel must never invent or leak a device reading."""
import json
from pathlib import Path
import tempfile
import unittest

from endgame4k_transaction import last_progress


class ProgressValidationTests(unittest.TestCase):
    def test_progress_write_failure_does_not_change_validated_result(self):
        from unittest.mock import patch
        import endgame4k_worker
        from providers import endgame_4k
        original = Path.write_text

        def write(path, *args, **kwargs):
            if path.name == 'progress.tmp':
                raise OSError('PRIVATE-PATH')
            return original(path, *args, **kwargs)

        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'request.json').write_text(json.dumps({
                'path_hex': b'PRIVATE-PATH'.hex(), 'product_id': 0x1970,
                'simulate': 'success',
            }), encoding='utf-8')
            with patch.object(Path, 'write_text', write), patch.object(endgame_4k, 'hid'):
                endgame4k_worker.main(folder)
            result = json.loads((directory / 'result.json').read_text(encoding='utf-8'))
            self.assertEqual(result['status']['level'], 45)
            self.assertNotIn('PRIVATE', str(result))
            self.assertFalse((directory / 'progress.json').exists())

    def test_missing_progress_is_explicitly_unknown(self):
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(last_progress(Path(folder)), 'last worker operation unavailable')

    def test_corrupt_or_untrusted_progress_is_not_echoed(self):
        cases = ['PRIVATE-PATH', '[]', 'null', '{', 'x' * 257,
                 json.dumps({'operation': 'PRIVATE-PATH', 'command': 14}),
                 json.dumps({'operation': 'read', 'command': 'PRIVATE-SERIAL'}),
                 json.dumps({'operation': 'read', 'command': True}),
                 json.dumps({'operation': 'read', 'command': 15})]
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            for value in cases:
                with self.subTest(value=value):
                    (directory / 'progress.json').write_text(value, encoding='utf-8')
                    self.assertEqual(last_progress(directory), 'last worker operation unavailable')

    def test_valid_progress_emits_only_operation_and_allowed_command(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            (directory / 'progress.json').write_text(json.dumps({
                'operation': 'read', 'command': 180, 'payload': 'PRIVATE-PAYLOAD',
            }), encoding='utf-8')
            value = last_progress(directory)
            self.assertIn('operation=read command=0xb4', value)
            self.assertNotIn('PRIVATE', value)
            self.assertIn('not proof of root cause', value)
