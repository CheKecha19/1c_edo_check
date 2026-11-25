"""Read-only client for the tenant API of VMware Cloud Director."""
import re
from urllib.parse import quote, urlsplit
from xml.etree import ElementTree

import requests


class VcdError(Exception):
    """Safe to display in the workbook. Never contains response bodies or tokens."""


class IncompletePage(VcdError):
    pass


class TemporaryFailure(VcdError):
    pass


class VcdClient:
    def __init__(self, site, tenant, api_token, session):
        for key, value in (("VCD_SITE", site), ("VCD_TENANT", tenant), ("VCD_API_TOKEN", api_token)):
            if not value:
                raise VcdError(f"Не задан {key}")
        url = urlsplit(site)
        if (url.scheme != "https" or not url.hostname or url.username is not None
                or url.path.strip("/") or url.query or url.fragment):
            raise VcdError("VCD_SITE должен содержать только HTTPS-адрес площадки")
        if tenant in (".", "..") or "/" in tenant:
            raise VcdError("VCD_TENANT должен содержать имя организации из ссылки входа")
        self.site = site.rstrip("/")
        self.tenant = tenant
        self.api_token = api_token
        self.session = session
        self.access_token = None
        self.version = None

    def _request(self, method, path, **kwargs):
        try:
            return self.session.request(method, self.site + path, timeout=(10, 30),
                                        allow_redirects=False, **kwargs)
        except requests.RequestException as error:
            raise TemporaryFailure(f"Ошибка соединения с VMware ({type(error).__name__})") from None

    @staticmethod
    def _check(response):
        if response.status_code == 429 or 500 <= response.status_code < 600:
            raise TemporaryFailure(f"VMware вернул HTTP {response.status_code}")
        if response.status_code != 200:
            raise VcdError(f"VMware вернул HTTP {response.status_code}")

    @staticmethod
    def _json(response):
        try:
            result = response.json()
        except ValueError:
            raise VcdError("VMware вернул некорректный JSON") from None
        if not isinstance(result, dict):
            raise VcdError("Неизвестный формат ответа VMware")
        return result

    def connect(self):
        response = self._request("GET", "/api/versions")
        self._check(response)
        try:
            root = ElementTree.fromstring(response.content)
        except ElementTree.ParseError:
            raise VcdError("Невозможно прочитать XML версий VMware") from None
        versions = []
        for node in root.findall(".//{*}Version"):
            value = (node.text or "").strip()
            if re.fullmatch(r"\d+\.\d+", value):
                version = tuple(map(int, value.split(".")))
                if (35, 0) <= version <= (39, 1):
                    versions.append((version, value))
        if not versions:
            raise VcdError("Площадка не объявила совместимую версию API (35.0–39.1)")
        self.version = max(versions)[1]
        self._login()

    def _login(self):
        path = "/oauth/tenant/" + quote(self.tenant, safe="") + "/token"
        response = self._request("POST", path, data={"grant_type": "refresh_token",
                                                     "refresh_token": self.api_token})
        self._check(response)
        token = self._json(response).get("access_token")
        if not isinstance(token, str) or not token.strip():
            raise VcdError("В ответе VMware отсутствует access_token")
        self.access_token = token

    def _get_page(self, path, page):
        for attempt in range(2):
            headers = {"Authorization": f"Bearer {self.access_token}",
                       "Accept": f"application/json;version={self.version}"}
            response = self._request("GET", path, headers=headers,
                                     params={"page": page, "pageSize": 100})
            if response.status_code == 401 and attempt == 0:
                self._login()
                continue
            self._check(response)
            return self._json(response)

    def collection(self, path):
        # Restart from page one to avoid mixing partial snapshots across attempts.
        try:
            return self._read_collection(path)
        except (IncompletePage, TemporaryFailure):
            return self._read_collection(path)

    def _read_collection(self, path):
        records = {}
        page = 1
        expected = None
        while True:
            data = self._get_page(path, page)
            count, total = data.get("pageCount"), data.get("resultTotal")
            values = data.get("values")
            if (data.get("page") != page or type(count) is not int or count < 0
                    or not isinstance(values, list)
                    or (total is not None and (type(total) is not int or total < 0))):
                raise IncompletePage("Некорректные сведения о странице VMware")
            if expected is not None and expected != (count, total):
                raise IncompletePage("Во время выгрузки изменился размер списка VMware")
            expected = (count, total)
            for item in values:
                key = item.get("id") if isinstance(item, dict) else None
                if not isinstance(key, str) or not key.strip() or key in records:
                    raise IncompletePage("Пустой или повторяющийся ID в списке VMware")
                records[key] = item
            if not values and (page > 1 or count > 1):
                raise IncompletePage("Пустая страница в неполном списке VMware")
            if page >= count:
                if (count == 0 and records) or (total is not None and len(records) != total):
                    raise IncompletePage("Число записей VMware не совпадает с итогом API")
                return list(records.values())
            page += 1

    def users(self):
        return self.collection("/cloudapi/1.0.0/users")

    def rights(self, role_id):
        path = "/cloudapi/1.0.0/roles/" + quote(role_id, safe=":") + "/rights"
        return self.collection(path)
