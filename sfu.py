import os
import requests
from bs4 import BeautifulSoup

BASE_URL = "https://e.sfu-kras.ru"

session = requests.Session()

# 1. Получаем страницу входа
response = session.get(
    f"{BASE_URL}/login/index.php",
    timeout=15
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

# 2. Извлекаем динамический токен
token_input = soup.select_one(
    'form#login input[name="logintoken"]'
)

if token_input is None:
    raise RuntimeError("Не найден logintoken")

# 3. Авторизуемся
response = session.post(
    f"{BASE_URL}/login/index.php",
    data={
        "username": os.environ["SFU_USERNAME"],
        "password": os.environ["SFU_PASSWORD"],
        "logintoken": token_input["value"],
        "anchor": "",
    },
    timeout=15
)
response.raise_for_status()

# 4. Проверяем авторизацию
response = session.get(
    f"{BASE_URL}/my/",
    timeout=15
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

if soup.select_one('form#login input[name="password"]'):
    raise RuntimeError("Авторизация не удалась")

print("Страница личного кабинета получена")

course_id = 41121

response = session.get(
    f"{BASE_URL}/grade/report/user/index.php",
    params={"id": course_id},
    timeout=15
)
response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")
table = soup.select_one("table.user-grade")

if table is None:
    raise RuntimeError(
        "Таблица оценок не найдена. Проверь авторизацию и доступ к курсу."
    )

grades = []

for row in table.select("tr"):
    cells = row.find_all(["th", "td"], recursive=False)

    if len(cells) < 3:
        continue

    name_cell = cells[0]
    grade_cell = cells[1]

    # Удаляем ненужные элементы интерфейса
    for element in name_cell.select(".accesshide, .sr-only"):
        element.decompose()

    for element in grade_cell.select("a, button, .action-menu"):
        element.decompose()

    name = name_cell.get_text(" ", strip=True)
    grade = grade_cell.get_text(" ", strip=True)
    grade_range = cells[2].get_text(" ", strip=True)

    if name == "Элемент оценивания":
        continue

    # Убираем технические надписи
    for prefix in (
        "Итоговая оценка ",
        "Вычисляемая оценка ",
        "Задание ",
        "Тест ",
    ):
        if name.startswith(prefix):
            name = name[len(prefix):]
            break

    grade = grade.replace("Действия", "")
    grade = grade.replace("Анализ оценок", "").strip()

    grades.append({
        "name": name,
        "grade": None if grade in ("", "-") else grade,
        "range": grade_range,
    })

for item in grades:
    print(
        f"{item['name']}: "
        f"{item['grade'] or 'Нет оценки'} "
        f"({item['range']})"
    )

print("\nЗАДАНИЯ И ДЕДЛАЙНЫ\n")

response = session.get(
    f"{BASE_URL}/course/overview.php",
    params={
        "id": 41121,
        "expand[]": "assign"
    },
    timeout=15
)

response.raise_for_status()

soup = BeautifulSoup(response.text, "html.parser")

table = soup.select_one("table.course-overview-table")

if table is None:
    raise RuntimeError("Таблица заданий не найдена")

for row in table.select("tbody tr"):
    cells = row.find_all(["th", "td"], recursive=False)

    if len(cells) < 4:
        continue

    name = cells[0].get_text(" ", strip=True)
    deadline = cells[1].get_text(" ", strip=True)
    status = cells[2].get_text(" ", strip=True)
    grade = cells[3].get_text(" ", strip=True)

    print(f"\n{name}")
    print(f"  Дедлайн: {deadline or 'Не указан'}")
    print(f"  Статус: {status or 'Неизвестен'}")
    print(f"  Оценка: {grade or 'Нет оценки'}")

    import json
from bs4 import BeautifulSoup

def get_courses(session):
    # Получаем sesskey из авторизованной страницы Moodle
    response = session.get(
        "https://e.sfu-kras.ru/my/",
        timeout=15
    )
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Moodle обычно содержит sesskey в настройках страницы.
    import re

    match = re.search(
        r'"sesskey"\s*:\s*"([^"]+)"',
        response.text
    )

    if not match:
        raise RuntimeError("Не удалось получить sesskey")

    sesskey = match.group(1)
    courses = []
    offset = 0
    limit = 48

    while True:
        payload = [{
            "index": 0,
            "methodname": "core_course_get_enrolled_courses_by_timeline_classification",
            "args": {
                "offset": offset,
                "limit": limit,
                "classification": "all",
                "sort": "fullname",
                "customfieldname": ""
            }
        }]

        response = session.post(
            "https://e.sfu-kras.ru/lib/ajax/service.php",
            params={
                "sesskey": sesskey,
                "info": "core_course_get_enrolled_courses_by_timeline_classification"
            },
            json=payload,
            timeout=20
        )
        response.raise_for_status()

        result = response.json()[0]

        if result.get("error"):
            raise RuntimeError(
                f"Ошибка Moodle: {result.get('exception')}"
            )

        data = result["data"]
        batch = data.get("courses", [])
        courses.extend(batch)

        if len(batch) < limit:
            break

        offset = data.get("nextoffset", offset + limit)

    return courses


courses = get_courses(session)

print(f"\nНайдено курсов: {len(courses)}\n")

for course in courses:
    print(f"{course['id']}: {course['fullname']}")
