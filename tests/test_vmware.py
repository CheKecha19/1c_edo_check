import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd

import config
import excel_processor as report
import main
from api_clients.vmware_client import VcdClient, VcdError
from assessment import ACTIVE, DISABLED, UNKNOWN_STATUS
from processors import vmware_processor as vmware


def response(data=None, status=200):
    return Mock(status_code=status, json=Mock(return_value=data))


def page(number, count, total, values):
    return {"page": number, "pageCount": count, "resultTotal": total, "values": values}


class VcdTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.client = VcdClient("https://vcd.example", "test", "test-refresh", self.session)
        self.client.version = "38.1"
        self.client.access_token = "first"

    def test_version_and_refresh(self):
        versions = Mock(status_code=200, content=b'<SupportedVersions xmlns="urn:test"><VersionInfo>'
                        b'<Version>38.1</Version></VersionInfo></SupportedVersions>')
        self.session.request.side_effect = [versions, response({"access_token": "old"}),
                                            response(status=401), response({"access_token": "new"}),
                                            response(page(1, 0, 0, []))]
        self.client.connect()
        self.assertEqual(self.client.users(), [])
        calls = self.session.request.call_args_list
        self.assertEqual(calls[1].args, ("POST", "https://vcd.example/oauth/tenant/test/token"))
        self.assertEqual(calls[1].kwargs["data"]["grant_type"], "refresh_token")
        self.assertFalse(calls[1].kwargs["allow_redirects"])
        self.assertEqual(calls[-1].kwargs["headers"], {
            "Accept": "application/json;version=38.1", "Authorization": "Bearer new"})

    def test_paging(self):
        pages = [page(1, 2, 2, [{"id": "a"}]), page(2, 2, 2, [{"id": "b"}])]
        self.session.request.side_effect = [response(p) for p in pages]
        self.assertEqual([u["id"] for u in self.client.users()], ["a", "b"])
        self.assertEqual(self.session.request.call_args.kwargs["params"]["page"], 2)

    def test_reject_partial_snapshot(self):
        start = response(page(1, 2, 2, [{"id": "a"}]))
        for last in (page(2, 2, 3, [{"id": "b"}]), page(2, 2, 2, [{"id": "a"}]),
                     page(2, 2, 2, []), page(2, 2, 2, [{}])):
            with self.subTest(last=last):
                self.session.request.side_effect = [start, response(last)] * 2
                with self.assertRaises(VcdError):
                    self.client.users()

    def test_transient_page_failure_restarts_and_permanent_failure_stops(self):
        first = response(page(1, 2, 2, [{"id": "1"}]))
        second = response(page(2, 2, 2, [{"id": "2"}]))
        self.session.request.side_effect = [first, response(status=503), first, second]
        self.assertEqual(len(self.client.users()), 2)
        self.session.request.side_effect = [response(status=403)]
        with self.assertRaisesRegex(VcdError, "403"):
            self.client.users()

    def test_tenant_path_and_settings(self):
        with self.assertRaisesRegex(VcdError, "VCD_API_TOKEN"):
            VcdClient("https://vcd.example", "test", "", self.session)
        with self.assertRaisesRegex(VcdError, "VCD_SITE"):
            VcdClient("http://vcd.example", "test", "x", self.session)


class VMwareReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "report.xlsx"
        for mod, settings in [
            (config, dict(VCD_SITE="https://vcd.example", VCD_TENANT="test",
                          VCD_INCLUDE_RIGHTS=True, VCD_API_TOKEN="synthetic")),
            (report, dict(OUTPUT_FILE=self.path, VCD_TENANT="test", USE_VMWARE_API=False,
                          VCD_INCLUDE_RIGHTS=True)),
        ]:
            ctx = patch.multiple(mod, **settings)
            ctx.start()
            self.addCleanup(ctx.stop)
        for target, value in [("load_shtat_data", pd.DataFrame({"Штатное_ФИО": ["Анна Павлова"]})),
                              ("read_ad_list", [])]:
            ctx = patch.object(report, target, return_value=value)
            ctx.start()
            self.addCleanup(ctx.stop)
        ctx = patch.object(vmware, "VcdClient", autospec=True)
        self.client = ctx.start().return_value
        self.addCleanup(ctx.stop)
        self.client.tenant = "test"

    def run_report(self, options=None):
        report.process_excel_data(options or {4})
        return pd.read_excel(self.path, sheet_name=None, keep_default_na=False)

    def test_accounts_and_role_rights(self):
        shared = {"id": "role-1", "name": "Общая"}
        other = {"id": "role-2", "name": "Вторая"}
        self.client.users.return_value = [
            {"id": "a", "username": "anna", "fullName": "Анна Павлова", "enabled": True,
             "roleEntityRefs": [shared, other]},
            {"id": "b", "username": "=TEST()", "fullName": "Иван Петров", "enabled": True,
             "locked": True, "isGroupRole": True, "roleEntityRefs": [shared]},
            {"id": "c", "enabled": False, "fullName": "Олег Минин", "roleEntityRefs": []},
            {"id": "d", "fullName": "", "username": "unmatched", "enabled": True},
            {"id": "e", "fullName": "Петр Миронов", "enabled": "false", "stranded": True},
        ]
        self.client.rights.side_effect = [[{"id": "right", "name": "Просмотр"}], VcdError("HTTP 403")]
        sheets = self.run_report()
        users = sheets["Пользователи VMware"].set_index("ID учётки")
        self.assertEqual(users.loc["b", "Блокировка входа"], "Да")
        self.assertEqual(users.loc["b", "Наследование от группы"], "Да")
        self.assertEqual(users.loc["e", "Потерян провайдер"], "Да")
        self.assertEqual(users.loc["c", "Роли"], "Нет назначений")
        self.assertEqual(users.loc["d", "Роли"], UNKNOWN_STATUS)
        self.assertEqual(users.loc["a", "ID ролей"], "role-1\nrole-2")
        self.assertEqual(self.client.rights.call_count, 2)
        rights = sheets["Права ролей VMware"].set_index("ID роли")
        self.assertEqual(rights.loc["role-2", "Состояние"], "HTTP 403")
        self.assertEqual(rights.loc["role-1", "Право"], "Просмотр")
        audit = sheets["Оценка доступа"].set_index("ID учётки")
        self.assertEqual(len(audit), 5)
        self.assertEqual(audit.loc["a", "Основание"], "Штатка")
        self.assertEqual(list(audit.loc[["a", "c", "e"], "Статус учётки"]), [ACTIVE, DISABLED, UNKNOWN_STATUS])
        self.assertEqual(set(sheets["Недостаточно данных"]["ID учётки"]), {"d", "e"})
        self.assertEqual(list(sheets["удалить из Cloud.ru VMware"]["ID учётки"]), ["b"])
        self.assertEqual(audit.loc["b", "Логин"], "=TEST()")

    def test_empty_vs_unavailable_and_other_services(self):
        self.client.users.return_value = []
        sheets = self.run_report()
        source = sheets["Источники"].iloc[-1]
        self.assertEqual((source["Состояние"], source["Записей"]), ("Проверен", 0))
        self.client.users.side_effect = VcdError("HTTP 403")
        data = pd.DataFrame({"1C_ФИО": ["Анна Павлова"], "1C_Активен": ["Да"]})
        with patch.object(report, "load_onec_data_new_format", return_value=data):
            sheets = self.run_report({1, 4})
        self.assertEqual(sheets["Источники"].iloc[-1]["Причина"], "HTTP 403")
        self.assertNotIn("удалить из Cloud.ru VMware", sheets)
        self.assertEqual(list(sheets["Оценка доступа"]["Система"]), ["1С"])

    def test_all_is_opt_in_and_explicit_menu_choice(self):
        self.client.users.return_value = []
        with patch.object(report, "get_kontur_data_source", return_value=pd.DataFrame(columns=["Контур_Диадок_ФИО"])), \
             patch.object(report, "get_diadoc_data_source", return_value=pd.DataFrame(columns=["Сфера_Курьер_ФИО"])), \
             patch.object(report, "load_onec_data_new_format", return_value=pd.DataFrame(columns=["1C_ФИО"])):
            self.run_report({0})
            self.client.connect.assert_not_called()
            with patch.object(report, "USE_VMWARE_API", True):
                self.run_report({0})
            self.client.connect.assert_called_once()
        with patch("builtins.input", return_value="4"):
            self.assertEqual(main.get_user_choice(), {4})
