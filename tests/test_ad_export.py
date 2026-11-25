import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import ad_export


def user(name, path, enabled=True):
    return {'Name': name, 'Enabled': enabled, 'DistinguishedName': path}


def snapshot(users, count=None):
    payload = {'Users': users, 'Count': len(users) if count is None else count}
    return ('Сообщение PowerShell\r\n' + ad_export.EXPORT_MARKER + json.dumps(payload) + '\r\n').encode()


class DirectoryExportTests(unittest.TestCase):
    @patch.object(ad_export, 'logger')
    @patch('ad_export.subprocess.run')
    def test_complete_export_and_empty_contractor_category(self, run, _logger):
        employee = user('Штатный сотрудник', r'CN=Сотрудник\,OU=ГПХ,OU=cu_users,DC=test')
        contractor = user('Исполнитель ГПХ', 'CN=Исполнитель,OU=external_organizations,OU=cu_users,DC=test')
        disabled = user('Заблокированный', 'CN=Заблокированный,OU=ГПХ,DC=test', enabled=False)
        process = run.return_value
        process.returncode = 0
        with TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(ad_export, 'OUTPUT_DIR', root), patch.object(ad_export, 'AD_EXPORT_DIR', root / 'AD'):
                process.stdout = snapshot([employee, contractor, disabled])
                self.assertEqual(ad_export.export_ad_users(), (3, 1, 1))
                contractors = root / 'AD' / 'ГПХ.txt'
                self.assertEqual(contractors.read_text(encoding='utf-8'), 'Name: Исполнитель ГПХ\nStatus: Активна\n\n')
                process.stdout = snapshot([employee, disabled])
                self.assertEqual(ad_export.export_ad_users(), (2, 1, 0))
                self.assertEqual(contractors.read_bytes(), b'')

    @patch('ad_export.subprocess.run')
    def test_rejects_unreliable_snapshots(self, run):
        record = user('Штатный сотрудник', 'CN=Сотрудник,OU=cu_users,DC=test')
        for label, data in {
            'incomplete JSON': b'AD_EXPORT_JSON:{"Count":2,"Users":[',
            'count mismatch': snapshot([record], count=2),
            'missing status': snapshot([{**record, 'Enabled': None}]),
            'string status': snapshot([{**record, 'Enabled': 'False'}]),
            'missing DN': snapshot([{**record, 'DistinguishedName': ''}]),
            'empty directory': snapshot([]),
        }.items():
            with self.subTest(label):
                run.return_value.returncode = 0
                run.return_value.stdout = data
                with self.assertRaises(ValueError):
                    ad_export.export_ad_users()

        run.return_value.returncode = 1
        run.return_value.stderr = b'AD unavailable'
        run.return_value.stdout = snapshot([record])
        with self.assertRaisesRegex(RuntimeError, 'AD unavailable'):
            ad_export.export_ad_users()
