import unittest
from unittest.mock import patch
from xml.etree import ElementTree

import pandas as pd

from api_clients.diadoc_api_client import DiadocAPIClient
from api_clients.sfera_api_client import SferaAPIClient
from utils import load_kontur_data_from_excel


class ServiceStatusTests(unittest.TestCase):
    def test_missing_date_column_differs_from_empty_date(self):
        frame = pd.DataFrame({"ФИО": ["Иванов Иван"] * 3,
                              "Дата блокировки": ["", "2025-01-14", "неизвестно"]})
        with patch("utils.get_kontur_file", return_value="export.xlsx"):
            with patch("utils.pd.read_excel", return_value=frame):
                status = load_kontur_data_from_excel()["Контур_Диадок_статус"]
                self.assertEqual(list(status), ["активна", "заблокирована", "неизвестно"])
            with patch("utils.pd.read_excel", return_value=frame[["ФИО"]]):
                status = load_kontur_data_from_excel()["Контур_Диадок_статус"]
                self.assertEqual(list(status), ["неизвестно"] * 3)

    def test_diadoc_api_missing_flag(self):
        client = DiadocAPIClient.__new__(DiadocAPIClient)
        user = {"FullName": {"FirstName": "Анна", "LastName": "Орлова"}}
        permissions = [{}, {"IsBlocked": False}, {"IsBlocked": True}]
        records = [{"User": user, "Permissions": {"AuthorizationPermission": p}}
                   for p in permissions]
        status = client.employees_to_dataframe(records)["Контур_Диадок_статус"]
        self.assertEqual(list(status), ["неизвестно", "активна", "заблокирована"])

    def test_sfera_api_missing_flag(self):
        client = SferaAPIClient.__new__(SferaAPIClient)
        users = []
        for element in ["", "<IsActive/>", "<IsActive>true</IsActive>", "<IsActive>false</IsActive>"]:
            xml = "<UserDetails><Person><FirstName>Анна</FirstName></Person>" + element + "</UserDetails>"
            users.append(client._parse_user_details(ElementTree.fromstring(xml)))
        with patch.object(client, "_get_admin_role_id", return_value=1):
            status = client.userlist_to_dataframe(users)["Сфера_Курьер_Активен"]
        self.assertEqual(list(status), ["Неизвестно", "Неизвестно", "Да", "Нет"])
