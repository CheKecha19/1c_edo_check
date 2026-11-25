from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from processors import onec_processor


class OnecStatusTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / 'users.xlsx'
        self.book = Workbook()
        self.sheet = self.book.active
        self.sheet.title = 'Лист_1'

    def load(self):
        self.book.save(self.path)
        with patch.object(onec_processor, 'get_onec_file', return_value=self.path):
            return onec_processor.load_onec_data_new_format()

    def test_merged_status_and_reordered_name_column(self):
        self.sheet.append(['Параметры отчёта'])
        self.sheet.append([None, 'Пользователь', None, 'Недействителен', None])
        for name, invalid in [('Первый', ' Да '), ('Второй', 'нЕт'),
                              ('Третий', None), ('Четвёртый', '?'), ('Робот', 'Нет')]:
            self.sheet.append([None, name, None, invalid, None])
        for number in range(2, 8):
            self.sheet.merge_cells(f'D{number}:E{number}')

        data = self.load()
        self.assertEqual(data['1C_ФИО'].tolist(), ['Первый', 'Второй', 'Третий', 'Четвёртый'])
        self.assertEqual(data['1C_Активен'].tolist(), ['Нет', 'Да', 'Неизвестно', 'Неизвестно'])
        self.assertEqual([u['is_active'] for u in onec_processor.parse_1c_users_report(self.path)],
                         [False, True, None, None, True])

    def test_missing_status_column(self):
        self.sheet.append(['Пользователь', 'Другое поле'])
        self.sheet.append(['Первый', 'Нет'])
        data = self.load()
        self.assertEqual(data['1C_Активен'].tolist(), ['Неизвестно'])

    def test_status_follows_header_when_export_layout_changes(self):
        self.sheet.append(['Недействителен', 'Пользователь', 'Ещё одно поле'])
        self.sheet.append(['Да', 'Первый', 'Нет'])
        self.assertEqual(self.load()['1C_Активен'].tolist(), ['Нет'])


if __name__ == '__main__':
    unittest.main()
