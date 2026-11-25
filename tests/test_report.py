from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import pandas as pd

from assessment import ACTIVE, INACTIVE, OK, REVIEW, UNKNOWN, UNKNOWN_STATUS
import excel_processor as report
import utils


class ReportTests(unittest.TestCase):
    staff = "Соколова Мария"
    ad_only = "Волков Иван"
    contractor = "Орлова Анна"
    unreadable = "Петров Алексей"
    inactive = "Сидорова Ольга"

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.output = self.base / "output.xlsx"
        self.roster = self.base / "roster" / "staff.xlsx"
        self.service = self.base / "service" / "1c.xlsx"
        self.roster.parent.mkdir()
        self.service.parent.mkdir()
        self.ad = self.base / "staff.txt"
        self.gph = self.base / "gph.txt"
        for module, attr, value in (
            (report, "OUTPUT_FILE", self.output), (report, "EMPLOYEES_FILE", self.ad),
            (report, "GPH_FILE", self.gph), (utils, "SHTAT_DIR", self.roster.parent),
            (utils, "ONEC_DIR", self.service.parent),
        ):
            change = patch.object(module, attr, value)
            change.start()
            self.addCleanup(change.stop)
        pd.DataFrame({"Ф.И.О.": [self.staff]}).to_excel(self.roster, index=False)
        pd.DataFrame({
            "Пользователь": [self.staff, self.ad_only, self.contractor, self.unreadable, self.inactive],
            "Недействителен": ["Нет", "Нет", "Нет", None, "Да"],
        }).to_excel(self.service, sheet_name="Лист_1", index=False)
        self.ad.write_text(f"Name: {self.ad_only}\nStatus: Активна\n", encoding="utf-8")
        self.gph.write_text(f"Name: {self.contractor}\nStatus: Активна\n", encoding="utf-8")

    def get_report(self, counts=(1, 1), categories=None):
        report.process_excel_data({1}, categories, ad_counts=counts)
        return pd.read_excel(self.output, sheet_name=None, keep_default_na=False)

    def test_full_report(self):
        sheets = self.get_report()
        users = sheets["Оценка доступа"].set_index("ФИО")
        self.assertEqual(users.loc[self.staff, "Основание"], "Штатка")
        self.assertEqual(users.loc[self.staff, "Статус учётки"], ACTIVE)
        self.assertEqual(users.loc[self.staff, "Результат"], OK)
        self.assertEqual(users.loc[self.contractor, "Основание"], "ГПХ в AD")
        self.assertEqual(users.loc[self.ad_only, "Результат"], REVIEW)
        self.assertEqual(users.loc[self.inactive, "Результат"], INACTIVE)
        self.assertEqual(users.loc[self.unreadable, "Статус учётки"], UNKNOWN_STATUS)
        self.assertEqual(list(sheets["удалить из 1С"]["ФИО"]), [self.ad_only])
        self.assertEqual(list(sheets["Недостаточно данных"]["ФИО"]), [self.unreadable])
        self.assertEqual(set(sheets[report.COMPARISON_SHEET]["ФИО"]), {self.staff, self.ad_only})

    def test_missing_roster_keeps_gph_confirmation(self):
        self.roster.unlink()
        sheets = self.get_report()
        users = sheets["Оценка доступа"].set_index("ФИО")
        self.assertEqual(users.loc[self.contractor, "Результат"], OK)
        self.assertEqual(users.loc[self.ad_only, "Результат"], UNKNOWN)
        self.assertNotIn("удалить из 1С", sheets)

    def test_verified_empty_gph_differs_from_failed_export(self):
        self.gph.write_text("", encoding="utf-8")
        sheets = self.get_report(counts=(1, 0))
        sources = sheets["Источники"].set_index("Источник")
        self.assertEqual(sources.loc["ГПХ в AD", "Состояние"], "Проверен")
        self.assertEqual(sources.loc["ГПХ в AD", "Записей"], 0)
        self.assertIn(self.contractor, sheets["удалить из 1С"]["ФИО"].tolist())

        for counts in (None, (1, 1)):
            with self.subTest(counts=counts):
                sheets = self.get_report(counts=counts)
                users = sheets["Оценка доступа"].set_index("ФИО")
                self.assertEqual(users.loc[self.staff, "Результат"], OK)
                self.assertEqual(users.loc[self.contractor, "Результат"], UNKNOWN)
                self.assertNotIn("удалить из 1С", sheets)

    def test_category_filter_keeps_unclassified_rows(self):
        for category, included, excluded in (
            (1, self.staff, self.contractor), (2, self.contractor, self.staff),
        ):
            with self.subTest(category=category):
                sheets = self.get_report(categories={category})
                users = sheets["Оценка доступа"]["ФИО"].tolist()
                self.assertIn(included, users)
                self.assertNotIn(excluded, users)
                self.assertIn(self.unreadable, users)
                self.assertEqual(list(sheets["удалить из 1С"]["ФИО"]), [self.ad_only])

    def test_empty_service(self):
        pd.DataFrame(columns=["Пользователь", "Недействителен"]).to_excel(
            self.service, sheet_name="Лист_1", index=False,
        )
        sheets = self.get_report()
        self.assertTrue(sheets["Оценка доступа"].empty)
        sources = sheets["Источники"].set_index("Источник")
        self.assertEqual(sources.loc["1С", "Состояние"], "Недостаточно данных")
        self.assertNotIn("удалить из 1С", sheets)


if __name__ == "__main__":
    unittest.main()
