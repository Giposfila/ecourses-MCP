"""Явный live smoke-тест против реального сайта еКурсов СФУ.

Запускается ТОЛЬКО вручную и требует заданных переменных окружения
``SFU_USERNAME`` и ``SFU_PASSWORD``. Секреты не печатаются.

Пример:
    python smoke.py --course-id 41121
    python smoke.py            # проверит первый доступный курс
"""

from __future__ import annotations

import argparse
import json
import sys

from sfu_client import SFUClient, SFUError
from sfu_env import load_dotenv


def main() -> int:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Live smoke-тест клиента еКурсов СФУ")
    parser.add_argument("--course-id", type=int, default=None, help="ID курса для проверки")
    parser.add_argument("--deadlines-days", type=int, default=30)
    args = parser.parse_args()

    client = SFUClient()

    print("1) Авторизация...", file=sys.stderr)
    try:
        client.login()
    except SFUError as exc:
        print(f"ОШИБКА авторизации: {exc}", file=sys.stderr)
        return 2

    print("2) Курсы...", file=sys.stderr)
    try:
        courses = client.get_courses()
    except SFUError as exc:
        print(f"ОШИБКА получения курсов: {exc}", file=sys.stderr)
        return 3

    print(f"   получено курсов: {len(courses)}", file=sys.stderr)
    for course in courses[:10]:
        print(f"   - {course['id']}: {course['name']}", file=sys.stderr)

    course_id = args.course_id or (courses[0]["id"] if courses else None)
    if course_id is None:
        print("Нет доступных курсов для проверки оценок/заданий.", file=sys.stderr)
        return 0

    print(f"3) Оценки курса {course_id}...", file=sys.stderr)
    try:
        grades = client.get_grades(course_id)
        print(f"   строк оценок: {len(grades)}", file=sys.stderr)
        print(json.dumps(grades[:5], ensure_ascii=False, indent=2))
    except SFUError as exc:
        print(f"   предупреждение: {exc}", file=sys.stderr)

    print(f"4) Задания курса {course_id}...", file=sys.stderr)
    try:
        assignments = client.get_assignments(course_id)
        print(f"   заданий: {len(assignments)}", file=sys.stderr)
        print(json.dumps(assignments[:5], ensure_ascii=False, indent=2))
    except SFUError as exc:
        print(f"   предупреждение: {exc}", file=sys.stderr)

    print(f"5) Дедлайны на {args.deadlines_days} дней...", file=sys.stderr)
    report = client.get_upcoming_deadlines(args.deadlines_days)
    print(
        f"   найдено дедлайнов: {len(report['items'])}, "
        f"без даты: {len(report['undated'])}, ошибок курсов: {len(report['course_errors'])}",
        file=sys.stderr,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
