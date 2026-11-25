import unittest

from assessment import (
    ACTIVE, DISABLED, INACTIVE, OK, REVIEW, UNKNOWN, UNKNOWN_STATUS,
    NameIndex, assess_account, compare_staff,
)


class AssessmentTests(unittest.TestCase):
    def test_enabled_service_account_needs_one_confirmed_source(self):
        roster = NameIndex(["Петров Пётр Алексеевич"])
        gph = NameIndex(["Орлова Анна Сергеевна"])
        for person, staff, contractors, expected in (
            ("петров  петр алексеевич", roster, None, "Штатка"),
            ("Орлова Анна", None, gph, "ГПХ в AD"),
        ):
            with self.subTest(person=person):
                result = assess_account(person, ACTIVE, staff, contractors)
                self.assertEqual(result["Результат"], OK)
                self.assertEqual(result["Основание"], expected)

    def test_absence_is_actionable_only_if_both_sources_available(self):
        empty = NameIndex([])
        person = "Сидоров Иван"
        for status, staff, gph, expected in (
            (ACTIVE, empty, empty, REVIEW), (DISABLED, empty, empty, INACTIVE),
            (ACTIVE, None, empty, UNKNOWN), (ACTIVE, empty, None, UNKNOWN),
            (ACTIVE, None, None, UNKNOWN),
        ):
            with self.subTest(status=status, staff=staff, gph=gph):
                self.assertEqual(assess_account(person, status, staff, gph)["Результат"], expected)

    def test_unknown_service_status(self):
        result = assess_account("Сидоров Иван", None, NameIndex(["Сидоров Иван"]), None)
        self.assertEqual(result, {
            "Основание": "Штатка", "Статус учётки": UNKNOWN_STATUS, "Результат": UNKNOWN,
        })

    def test_patronymic_distinguishes_people_across_sources(self):
        staff = NameIndex(["Сидоров Иван Иванович"])
        gph = NameIndex(["Сидоров Иван Петрович"])
        exact = assess_account("Сидоров Иван Петрович", ACTIVE, staff, gph)
        self.assertEqual(exact["Основание"], "ГПХ в AD")
        self.assertEqual(assess_account("Сидоров Иван", ACTIVE, staff, gph)["Результат"], UNKNOWN)
        self.assertEqual(assess_account("Сидоров Иван Ильич", ACTIVE, staff, gph)["Результат"], REVIEW)

    def test_short_reference_cannot_confirm_two_different_service_users(self):
        staff = NameIndex(["Петров Иван"])
        service = NameIndex(["Петров Иван Ильич", "Петров Иван Олегович"])
        for name in service.names.values():
            result = assess_account(name, ACTIVE, staff, NameIndex([]), service)
            self.assertEqual(result["Результат"], UNKNOWN)

    def test_short_query_across_two_references(self):
        person = "Орлова Анна"
        full = "Орлова Анна Сергеевна"
        result = assess_account(person, ACTIVE, NameIndex([full]), NameIndex([person]))
        self.assertEqual(result["Результат"], UNKNOWN)
        same = assess_account(person, ACTIVE, NameIndex([full]), NameIndex([full]))
        self.assertEqual(same["Основание"], "Штатка + ГПХ в AD")

    def test_repeated_full_name_and_incomplete_name(self):
        name = "Сидоров Иван Иванович"
        duplicate = NameIndex([name, name])
        result = assess_account(name, ACTIVE, NameIndex([name]), None, duplicate)
        self.assertEqual(result["Результат"], UNKNOWN)
        for person in (name, "Сидоров Иван", "Сидоров", ""):
            with self.subTest(person=person):
                self.assertEqual(assess_account(person, ACTIVE, duplicate, None)["Результат"], UNKNOWN)

    def test_roster_and_active_ad_in_both_directions(self):
        roster = NameIndex(["Петров Пётр", "Сидоров Иван Ильич"])
        ad = NameIndex(["Петров Петр Петрович", "Орлова Анна Павловна"])
        diff = compare_staff(roster, ad)
        self.assertEqual({(row["ФИО"], row["Источник"]) for row in diff}, {
            ("Сидоров Иван Ильич", "Штатка"), ("Орлова Анна Павловна", "Штатные в AD"),
        })
        self.assertEqual(compare_staff(roster, None)[0]["Статус"], "Источник недоступен")


if __name__ == "__main__":
    unittest.main()
