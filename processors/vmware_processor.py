"""Translate a complete Cloud Director tenant snapshot into workbook rows."""
import re

import pandas as pd
import requests

import config
from api_clients.vmware_client import VcdClient, VcdError
from assessment import ACTIVE, DISABLED, UNKNOWN_STATUS


SYSTEM = "Cloud.ru VMware"
USER_COLUMNS = ["Площадка", "Тенант", "ID учётки", "Логин", "ФИО", "Email",
                "Статус VMware", "Блокировка входа", "Потерян провайдер",
                "Провайдер", "Роли", "ID ролей", "Наследование от группы"]
RIGHT_COLUMNS = ["Площадка", "Тенант", "ID роли", "Роль", "ID права", "Право", "Состояние"]


def text(value):
    if not isinstance(value, str):
        return ""
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", value).strip()


def flag(value, yes="Да", no="Нет"):
    if value is True:
        return yes
    if value is False:
        return no
    return UNKNOWN_STATUS


def load_vmware():
    context = {"Площадка": config.VCD_SITE, "Тенант": config.VCD_TENANT}
    users, rights, role_names = [], [], {}
    with requests.Session() as session:
        client = VcdClient(config.VCD_SITE, config.VCD_TENANT, config.VCD_API_TOKEN, session)
        client.connect()
        for user in client.users():
            org = user.get("orgEntityRef") or {}
            if isinstance(org, dict) and org.get("name") and org["name"].casefold() != client.tenant.casefold():
                raise VcdError("API вернул пользователя другого тенанта")
            roles = user.get("roleEntityRefs")
            ids, names = [], []
            if isinstance(roles, list):
                for ref in roles:
                    role_id = text(ref.get("id")) if isinstance(ref, dict) else ""
                    name = text(ref.get("name")) if isinstance(ref, dict) else ""
                    ids.append(role_id or UNKNOWN_STATUS)
                    names.append(name or UNKNOWN_STATUS)
                    if role_id:
                        role_names[role_id] = name or role_names.get(role_id, UNKNOWN_STATUS)
            users.append({
                **context, "ID учётки": user["id"], "Логин": text(user.get("username")),
                "ФИО": text(user.get("fullName")), "Email": text(user.get("email")),
                "Статус VMware": flag(user.get("enabled"), ACTIVE, DISABLED),
                "Блокировка входа": flag(user.get("locked")),
                "Потерян провайдер": flag(user.get("stranded")),
                "Провайдер": text(user.get("providerType")) or UNKNOWN_STATUS,
                "Роли": "\n".join(names) or ("Нет назначений" if roles == [] else UNKNOWN_STATUS),
                "ID ролей": "\n".join(ids),
                "Наследование от группы": flag(user.get("isGroupRole")),
            })
        if config.VCD_INCLUDE_RIGHTS:
            for role_id, name in role_names.items():
                base = {**context, "ID роли": role_id, "Роль": name}
                if name.casefold() == "defer to identity provider":
                    rights.append({**base, "Состояние": "Разрешения определяет внешний провайдер"})
                    continue
                try:
                    items = client.rights(role_id)
                except VcdError as error:
                    rights.append({**base, "Состояние": str(error)})
                    continue
                for item in items:
                    rights.append({**base, "ID права": item["id"], "Право": text(item.get("name")),
                                   "Состояние": "Прочитано"})
                if not items:
                    rights.append({**base, "Состояние": "В роли нет прав"})
    return pd.DataFrame(users, columns=USER_COLUMNS), pd.DataFrame(rights, columns=RIGHT_COLUMNS)
