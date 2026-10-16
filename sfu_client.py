"""HTTP-клиент еКурсов СФУ (Moodle).

Отделяет работу с сетью, сессией и авторизацией от парсеров и MCP-сервера.
Только чтение: клиент не выполняет изменяющих запросов.

Секреты (пароль, cookies, sesskey, logintoken) никогда не логируются и не
попадают в возвращаемые структуры.
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import time
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from sfu_parsers import (
    ParseError,
    classify_attachment,
    normalize_course,
    parse_assignment_details,
    parse_assignments,
    parse_courses_ajax_response,
    parse_grades,
)

__all__ = [
    "SFUError",
    "AuthenticationError",
    "SessionExpiredError",
    "CourseUnavailableError",
    "SFUClient",
]

log = logging.getLogger("sfu.client")

BASE_URL = "https://e.sfu-kras.ru"
ALLOWED_HOST = "e.sfu-kras.ru"
USER_AGENT = "sfu-ecourses-mcp/1.0 (read-only)"
MAX_COURSE_PAGES = 50
COURSES_PAGE_SIZE = 48
DEFAULT_DOWNLOAD_MAX_MB = 25
CHUNK_SIZE = 64 * 1024

_BAD_FILENAME_CHARS = re.compile(r'[\x00-\x1f<>:"/\\|?*]')


def download_dir() -> pathlib.Path:
    """Папка для скачанных материалов (``SFU_DOWNLOAD_DIR``)."""
    raw = os.environ.get("SFU_DOWNLOAD_DIR") or "~/Downloads/sfu-ecourses"
    return pathlib.Path(os.path.expanduser(raw)).resolve()


def download_max_bytes() -> int:
    """Лимит размера одного файла (``SFU_DOWNLOAD_MAX_MB``, по умолчанию 25 МБ)."""
    raw = os.environ.get("SFU_DOWNLOAD_MAX_MB")
    try:
        megabytes = int(raw) if raw else DEFAULT_DOWNLOAD_MAX_MB
    except (TypeError, ValueError):
        megabytes = DEFAULT_DOWNLOAD_MAX_MB
    return max(1, megabytes) * 1024 * 1024


def safe_filename(raw: str, fallback: str = "material") -> str:
    """Превращает имя из URL в безопасное имя файла без пути."""
    name = unquote(raw or "").replace("\\", "/").rsplit("/", 1)[-1]
    name = _BAD_FILENAME_CHARS.sub("_", name).strip().strip(".")
    if not name:
        return fallback
    if len(name) > 120:
        stem, dot, extension = name.rpartition(".")
        if dot and len(extension) <= 10:
            name = stem[:110] + dot + extension
        else:
            name = name[:120]
    return name or fallback


class SFUError(RuntimeError):
    """Базовая ошибка клиента еКурсов."""


class AuthenticationError(SFUError):
    """Не удалось авторизоваться (неверные данные, CAPTCHA и т.п.)."""


class SessionExpiredError(SFUError):
    """Сессия истекла и повторный вход не помог."""


class CourseUnavailableError(SFUError):
    """Курс недоступен или страница не содержит ожидаемых данных."""


def _validate_int(value: Any, name: str, minimum: int = 1) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{name} должен быть целым числом")
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{name} должен быть целым числом") from None
    if number < minimum:
        raise ValueError(f"{name} должен быть >= {minimum}")
    return number


class SFUClient:
    """Клиент личного кабинета студента на еКурсах СФУ."""

    def __init__(
        self,
        username: str | None = None,
        password: str | None = None,
        *,
        timeout: float = 20.0,
        cache_ttl: float = 300.0,
        max_retries: int = 2,
        min_interval: float = 0.2,
        session: requests.Session | None = None,
    ) -> None:
        self.username = username if username is not None else os.environ.get("SFU_USERNAME")
        self.password = password if password is not None else os.environ.get("SFU_PASSWORD")
        self.timeout = timeout
        self.cache_ttl = cache_ttl
        self.max_retries = max(0, int(max_retries))
        self.min_interval = max(0.0, float(min_interval))

        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", USER_AGENT)

        self._logged_in = False
        self._sesskey: str | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_at = 0.0

    # ------------------------------------------------------------------
    # Низкоуровневая работа с сессией
    # ------------------------------------------------------------------

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            if urlparse(path).hostname != ALLOWED_HOST:
                raise SFUError("Запрещённый хост запроса")
            return path
        return urljoin(BASE_URL + "/", path.lstrip("/"))

    def _throttle(self) -> None:
        if self.min_interval <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_request_at = time.monotonic()

    def _check_hosts(self, response: requests.Response) -> None:
        for item in list(response.history) + [response]:
            host = urlparse(item.url).hostname
            if host != ALLOWED_HOST:
                raise SFUError(f"Редирект на посторонний хост: {host}")

    def _network(
        self,
        method: str,
        path: str,
        *,
        params: dict | None = None,
        **kwargs,
    ) -> requests.Response:
        """Запрос с троттлингом и санитизацией ошибок.

        Текст исключений ``requests`` может содержать query-строку с секретами
        (например, ``sesskey``), поэтому наружу отдаём только безопасное описание.
        """
        self._throttle()
        try:
            response = self.session.request(
                method, self._url(path), params=params, timeout=self.timeout, **kwargs
            )
        except requests.RequestException as exc:
            raise SFUError(
                f"Сетевой сбой при обращении к {path} ({exc.__class__.__name__})"
            ) from None
        if response.status_code >= 400:
            raise SFUError(f"HTTP {response.status_code} при обращении к {path}")
        self._check_hosts(response)
        return response

    @staticmethod
    def _is_login_page(html: str, url: str = "") -> bool:
        if "login/index.php" in (url or ""):
            return True
        if not html:
            return False
        soup = BeautifulSoup(html, "html.parser")
        return soup.select_one('form#login input[name="password"]') is not None

    @staticmethod
    def _extract_login_token(html: str) -> str | None:
        soup = BeautifulSoup(html, "html.parser")
        node = soup.select_one('form#login input[name="logintoken"]')
        if node is not None and node.get("value"):
            return node["value"]
        return None

    @staticmethod
    def _extract_sesskey(html: str) -> str | None:
        patterns = (
            r'"sesskey"\s*:\s*"([^"]+)"',
            r"M\.cfg\.sesskey\s*=\s*\"([^\"]+)\"",
            r"sesskey=([A-Za-z0-9]+)",
            r'name="sesskey"\s+value="([^"]+)"',
        )
        for pattern in patterns:
            match = re.search(pattern, html)
            if match:
                return match.group(1)
        return None

    def login(self) -> None:
        """Выполняет вход и проверяет доступ к авторизованной странице."""
        if not self.username or not self.password:
            raise AuthenticationError(
                "Не заданы переменные окружения SFU_USERNAME и SFU_PASSWORD"
            )

        response = self._network("GET", "/login/index.php")

        if self._is_login_page(response.text, response.url):
            token = self._extract_login_token(response.text)
            if token is None:
                raise AuthenticationError(
                    "Не удалось получить logintoken со страницы входа. "
                    "Возможно, изменилась форма входа или требуется CAPTCHA."
                )
            response = self._network(
                "POST",
                "/login/index.php",
                data={
                    "username": self.username,
                    "password": self.password,
                    "logintoken": token,
                    "anchor": "",
                },
            )

        if not self._verify_login():
            raise AuthenticationError(
                "Авторизация не удалась. Проверьте логин/пароль, CAPTCHA или MFA."
            )

        self._logged_in = True
        self._sesskey = None
        log.info("Авторизация на еКурсах СФУ выполнена")

    def _verify_login(self) -> bool:
        response = self._network("GET", "/my/")
        return not self._is_login_page(response.text, response.url)

    def _ensure_login(self) -> None:
        if not self._logged_in:
            self.login()

    def _request(self, method: str, path: str, *, params: dict | None = None, **kwargs) -> requests.Response:
        """Выполняет запрос с однократным перевходом при истечении сессии."""
        self._ensure_login()
        attempt = 0
        while True:
            response = self._network(method, path, params=params, **kwargs)

            if not self._is_login_page(response.text, response.url):
                return response

            attempt += 1
            if attempt > self.max_retries:
                raise SessionExpiredError("Сессия истекла, повторный вход не помог")
            log.info("Сессия истекла, выполняю повторный вход")
            self._logged_in = False
            self.login()

    def _get_sesskey(self) -> str:
        if self._sesskey:
            return self._sesskey
        response = self._request("GET", "/my/")
        sesskey = self._extract_sesskey(response.text)
        if not sesskey:
            raise SFUError(
                "Не удалось извлечь sesskey из авторизованной страницы. "
                "Возможно, изменилась разметка Moodle."
            )
        self._sesskey = sesskey
        return sesskey

    def _post_ajax(self, methodname: str, payload: list[dict]) -> list[dict]:
        attempt = 0
        while True:
            sesskey = self._get_sesskey()
            response = self._network(
                "POST",
                "/lib/ajax/service.php",
                params={"sesskey": sesskey, "info": methodname},
                json=payload,
            )

            try:
                data = response.json()
            except ValueError:
                raise SFUError("Moodle вернул не-JSON ответ на AJAX-запрос") from None

            if isinstance(data, list) and data and self._ajax_needs_reauth(data[0]):
                attempt += 1
                if attempt > self.max_retries:
                    raise SessionExpiredError("Сессия истекла, повторный вход не помог")
                log.info("AJAX-сессия истекла, выполняю повторный вход")
                self._sesskey = None
                self._logged_in = False
                self.login()
                continue

            if not isinstance(data, list):
                raise SFUError("Неожиданный формат AJAX-ответа Moodle")
            return data

    @staticmethod
    def _ajax_needs_reauth(item: Any) -> bool:
        if not isinstance(item, dict):
            return False
        code = str(item.get("errorcode") or "").lower()
        return code in {"servicerequireslogin", "invalidsesskey", "sessionexpired"}

    # ------------------------------------------------------------------
    # Кэш
    # ------------------------------------------------------------------

    def _cache_get(self, key: str) -> Any | None:
        entry = self._cache.get(key)
        if not entry:
            return None
        stored_at, value = entry
        if time.monotonic() - stored_at > self.cache_ttl:
            self._cache.pop(key, None)
            return None
        return value

    def _cache_set(self, key: str, value: Any) -> None:
        self._cache[key] = (time.monotonic(), value)

    def clear_cache(self) -> None:
        self._cache.clear()

    # ------------------------------------------------------------------
    # Публичные инструменты
    # ------------------------------------------------------------------

    def get_courses(self, *, use_cache: bool = True) -> list[dict[str, Any]]:
        cache_key = "courses"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        courses: list[dict[str, Any]] = []
        seen: set[Any] = set()
        offset = 0

        for _ in range(MAX_COURSE_PAGES):
            payload = [
                {
                    "index": 0,
                    "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
                    "args": {
                        "offset": offset,
                        "limit": COURSES_PAGE_SIZE,
                        "classification": "all",
                        "sort": "fullname",
                        "customfieldname": "",
                    },
                }
            ]
            data = self._post_ajax(
                "core_course_get_enrolled_courses_by_timeline_classification", payload
            )
            raw_courses, error = parse_courses_ajax_response(data[0])
            if error:
                raise SFUError(
                    f"Ошибка Moodle при получении курсов: {error.get('exception')}"
                )
            if not raw_courses:
                break

            for raw in raw_courses:
                course = normalize_course(raw)
                if course["id"] in seen:
                    continue
                seen.add(course["id"])
                courses.append(course)

            result_data = data[0].get("data") or {}
            next_offset = result_data.get("nextoffset")
            if (
                not isinstance(next_offset, int)
                or next_offset <= offset
                or len(raw_courses) < COURSES_PAGE_SIZE
            ):
                break
            offset = next_offset

        if use_cache:
            self._cache_set(cache_key, courses)
        return courses

    def get_grades(self, course_id: int, *, use_cache: bool = True) -> list[dict[str, Any]]:
        course_id = _validate_int(course_id, "course_id")
        cache_key = f"grades:{course_id}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        response = self._request(
            "GET", "/grade/report/user/index.php", params={"id": course_id}
        )
        try:
            grades = parse_grades(response.text)
        except ParseError as exc:
            raise CourseUnavailableError(
                f"Курс {course_id}: {exc}. Возможен недоступный курс или истёкшая сессия."
            ) from None

        if use_cache:
            self._cache_set(cache_key, grades)
        return grades

    def get_assignments(self, course_id: int, *, use_cache: bool = True) -> list[dict[str, Any]]:
        course_id = _validate_int(course_id, "course_id")
        cache_key = f"assignments:{course_id}"
        if use_cache:
            cached = self._cache_get(cache_key)
            if cached is not None:
                return cached

        response = self._request(
            "GET",
            "/course/overview.php",
            params={"id": course_id, "expand[]": "assign"},
        )
        try:
            assignments = parse_assignments(response.text, now=datetime.now())
        except ParseError as exc:
            raise CourseUnavailableError(
                f"Курс {course_id}: {exc}. В курсе может не быть заданий."
            ) from None

        if use_cache:
            self._cache_set(cache_key, assignments)
        return assignments

    def get_assignment_details(self, module_id: int) -> dict[str, Any]:
        module_id = _validate_int(module_id, "module_id")
        response = self._request("GET", "/mod/assign/view.php", params={"id": module_id})
        details = parse_assignment_details(
            response.text, module_id=module_id, now=datetime.now()
        )
        if not details.get("title") and not details.get("submission_status_raw"):
            raise CourseUnavailableError(
                f"Элемент {module_id}: не удалось распознать страницу задания."
            )
        return details

    # ------------------------------------------------------------------
    # Материалы заданий (скачивание только файлов курса)
    # ------------------------------------------------------------------

    def list_assignment_materials(self, module_id: int) -> dict[str, Any]:
        """Список материалов задания. Ничего не скачивает и не изменяет.

        Возвращаются только вложения типа ``material`` (методички, конспекты).
        Личные файлы сдачи не раскрываются — по ним отдаётся только счётчик.
        """
        module_id = _validate_int(module_id, "module_id")
        details = self.get_assignment_details(module_id)
        attachments = details.get("attachments") or []

        materials = [
            {
                "name": item.get("name"),
                "url": item.get("url"),
                "kind": item.get("kind"),
            }
            for item in attachments
            if item.get("kind") == "material"
        ]

        return {
            "module_id": module_id,
            "title": details.get("title"),
            "download_dir": str(download_dir()),
            "materials": materials,
            "personal_files_count": sum(
                1 for item in attachments if item.get("kind") == "submission"
            ),
            "warnings": details.get("warnings", []),
        }

    def download_assignment_materials(
        self,
        module_id: int,
        *,
        names: list[str] | None = None,
        dest_dir: str | None = None,
        overwrite: bool = False,
        max_bytes: int | None = None,
    ) -> dict[str, Any]:
        """Скачивает материалы задания в локальную папку.

        Личные файлы сдачи не скачиваются никогда. Данные на сайте не меняются.
        """
        module_id = _validate_int(module_id, "module_id")
        listing = self.list_assignment_materials(module_id)
        target_dir = (
            pathlib.Path(dest_dir).expanduser().resolve() if dest_dir else download_dir()
        )
        limit = int(max_bytes) if max_bytes else download_max_bytes()
        wanted = {name.strip() for name in names if name and name.strip()} if names else None

        downloaded: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []

        for material in listing["materials"]:
            name = material.get("name") or ""
            url = material.get("url") or ""
            if wanted is not None and name not in wanted:
                continue
            if not url:
                errors.append({"name": name, "error": "у материала нет ссылки"})
                continue
            try:
                result = self._download_file(
                    url, target_dir, overwrite=overwrite, max_bytes=limit
                )
            except SFUError as exc:
                errors.append({"name": name, "error": str(exc)})
                continue
            if result.get("skipped"):
                skipped.append(result)
            else:
                downloaded.append(result)

        return {
            "module_id": module_id,
            "title": listing["title"],
            "download_dir": str(target_dir),
            "max_bytes": limit,
            "materials_total": len(listing["materials"]),
            "downloaded": downloaded,
            "skipped": skipped,
            "errors": errors,
            "notes": [
                "Скачиваются только материалы курса; личные файлы сдачи не загружаются.",
                "Сервер не отправляет ответы и не изменяет данные на сайте.",
            ],
        }

    def _download_file(
        self,
        url: str,
        target_dir: pathlib.Path,
        *,
        overwrite: bool,
        max_bytes: int,
    ) -> dict[str, Any]:
        parsed = urlparse(url)
        if parsed.hostname != ALLOWED_HOST:
            raise SFUError("Ссылка ведёт на посторонний хост")
        if classify_attachment(url) != "material":
            raise SFUError("Разрешено скачивать только материалы курса")

        filename = safe_filename(parsed.path.rsplit("/", 1)[-1])
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / filename

        if target.exists() and not overwrite:
            return {
                "name": filename,
                "path": str(target),
                "bytes": target.stat().st_size,
                "skipped": "already_exists",
            }

        response = self._download_response(url)
        try:
            size = self._write_stream(response, target, max_bytes=max_bytes)
        finally:
            response.close()

        log.info("Материал сохранён: %s (%d байт)", filename, size)
        return {"name": filename, "path": str(target), "bytes": size}

    def _download_response(self, url: str) -> requests.Response:
        """GET с потоковой отдачей. HTML вместо файла считается ошибкой."""
        self._ensure_login()
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                response = self.session.request(
                    "GET", self._url(url), timeout=self.timeout, stream=True
                )
            except requests.RequestException as exc:
                raise SFUError(
                    f"Сетевой сбой при скачивании файла ({exc.__class__.__name__})"
                ) from None

            if response.status_code >= 400:
                response.close()
                raise SFUError(f"HTTP {response.status_code} при скачивании файла")
            self._check_hosts(response)

            content_type = (response.headers.get("Content-Type") or "").lower()
            if "text/html" not in content_type:
                return response

            body = response.text
            response.close()
            if self._is_login_page(body, response.url) and attempt < self.max_retries:
                self._logged_in = False
                self.login()
                continue
            raise SFUError("Сайт вернул HTML вместо файла: доступ к материалу закрыт")

        raise SFUError("Не удалось скачать файл")

    @staticmethod
    def _write_stream(response: requests.Response, target: pathlib.Path, *, max_bytes: int) -> int:
        declared = response.headers.get("Content-Length")
        if declared and str(declared).isdigit() and int(declared) > max_bytes:
            raise SFUError(f"Файл больше лимита {max_bytes} байт")

        temporary = target.with_name(target.name + ".part")
        written = 0
        try:
            with temporary.open("wb") as handle:
                for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                    if not chunk:
                        continue
                    written += len(chunk)
                    if written > max_bytes:
                        raise SFUError(f"Файл больше лимита {max_bytes} байт")
                    handle.write(chunk)
            temporary.replace(target)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
        return written

    def get_upcoming_deadlines(
        self,
        days: int = 7,
        include_submitted: bool = False,
        include_overdue: bool = False,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        days = _validate_int(days, "days", minimum=1)
        if days > 365:
            days = 365
        now = now or datetime.now()
        horizon = now + timedelta(days=days)

        courses = self.get_courses()

        items: list[dict[str, Any]] = []
        overdue: list[dict[str, Any]] = []
        undated: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        skipped_submitted = 0

        for course in courses:
            course_id = course.get("id")
            course_name = course.get("name")
            if not isinstance(course_id, int):
                errors.append(
                    {
                        "course_id": course_id,
                        "course_name": course_name,
                        "error": "Некорректный id курса",
                    }
                )
                continue
            try:
                assignments = self.get_assignments(course_id)
            except Exception as exc:  # один курс не должен ломать общий отчёт
                errors.append(
                    {
                        "course_id": course_id,
                        "course_name": course_name,
                        "error": str(exc) or exc.__class__.__name__,
                    }
                )
                continue

            for assignment in assignments:
                base = {
                    "course_id": course_id,
                    "course_name": course_name,
                    "name": assignment.get("name"),
                    "module_id": assignment.get("module_id"),
                    "url": assignment.get("url"),
                    "submission_status": assignment.get("submission_status"),
                    "grade_raw": assignment.get("grade_raw"),
                }
                if assignment.get("submission_status") == "submitted" and not include_submitted:
                    skipped_submitted += 1
                    continue

                due_at = assignment.get("due_at")
                if not due_at:
                    # Задания без колонки срока сдачи не считаем «нераспознанными».
                    if assignment.get("due_date_raw"):
                        undated.append(
                            {
                                **base,
                                "due_date_raw": assignment.get("due_date_raw"),
                                "due_warnings": assignment.get("due_warnings", []),
                            }
                        )
                    continue

                due = datetime.fromisoformat(due_at)
                if due > horizon:
                    continue

                entry = {
                    **base,
                    "due_at": due_at,
                    "due_date_raw": assignment.get("due_date_raw"),
                    "due_warnings": assignment.get("due_warnings", []),
                    "is_overdue": due < now,
                    "days_left": (due - now).days,
                }
                if due < now and not include_overdue:
                    overdue.append(entry)
                else:
                    items.append(entry)

        items.sort(key=lambda item: (item["due_at"], item.get("course_name") or ""))
        overdue.sort(key=lambda item: (item["due_at"], item.get("course_name") or ""), reverse=True)

        return {
            "generated_at": now.isoformat(timespec="minutes"),
            "days": days,
            "include_submitted": bool(include_submitted),
            "include_overdue": bool(include_overdue),
            "items": items,
            "overdue": overdue,
            "undated": undated,
            "course_errors": errors,
            "skipped_submitted": skipped_submitted,
            "notes": [
                "due_at получен из абсолютного таймстампа сайта и приведён к локальному часовому поясу.",
                "Просроченные работы по умолчанию не попадают в items — они в overdue.",
                "Пустой статус сдачи сохраняется как unknown, а не как «не сдано».",
            ],
        }
