# SFU eCourses MCP Server

Локальный MCP-сервер (**только чтение**) для личного кабинета студента на
[еКурсах СФУ](https://e.sfu-kras.ru/) (Moodle). Позволяет ИИ-клиенту получать
список курсов, оценки, задания, дедлайны и детали отдельного задания.

Логика авторизации, URL и разбор HTML основаны на ранее проверенном скрипте
`sfu.py` (сохранён как backup и не запускается как часть сервера).

## Инструменты

| Инструмент | Аргументы | Что возвращает |
|---|---|---|
| `get_courses` | — | список курсов: `id`, `name`, `shortname`, `url`, категория, прогресс |
| `get_grades` | `course_id: int` | строки таблицы оценок: тип элемента, исходная/числовая оценка, диапазон, признак итогов, предупреждения |
| `get_assignments` | `course_id: int` | задания: название, `module_id`/`url`, раздел, срок сдачи, статус, оценка, предупреждения |
| `get_upcoming_deadlines` | `days: int = 7`, `include_submitted: bool = false`, `include_overdue: bool = false` | будущие дедлайны в `items`, просроченные — в `overdue`, нераспознанные сроки — в `undated`; плюс `course_errors` |
| `get_assignment_details` | `module_id: int` | детали задания: описание, срок, статус ответа/оценивания, вложения |

## Структура проекта

```text
MCP/
├── AGENT.md                # исходное задание
├── sfu.py                  # проверенный backup-скрипт (не используется сервером)
├── sfu_client.py           # SFUClient: сессия, логин, HTTP, кэш, сборка данных
├── sfu_parsers.py          # чистые парсеры HTML/JSON -> структуры
├── sfu_dates.py            # нормализация дат дедлайнов
├── server.py               # MCP-сервер (stdio) и объявления инструментов
├── smoke.py                # явный live-тест (ручной запуск)
├── sfu_env.py              # чтение локального .env
├── requirements.txt
├── .env.example
├── .gitignore
├── README.md
└── tests/
    ├── fixtures/
    ├── test_courses.py
    ├── test_grades.py
    ├── test_assignments.py
    ├── test_dates.py
    └── test_env.py
```

## Установка

Требуется Python 3.10+ (проверено на 3.14).

```bash
cd /home/pozumal/Projects/MCP
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Учётные данные

Сервер читает переменные окружения `SFU_USERNAME` и `SFU_PASSWORD`.
**Никогда не коммитьте `.env` в git и не передавайте пароль аргументом командной строки.**

### Вариант 1: файл `.env` (проще всего)

`server.py` и `smoke.py` автоматически читают `.env` из папки проекта.
Скопируйте шаблон и впишите свои данные:

```bash
cp .env.example .env
chmod 600 .env
$EDITOR .env   # SFU_USERNAME=... / SFU_PASSWORD=...
```

Приоритет: переменные, заданные явно в окружении, важнее значений из `.env`.

### Вариант 2: переменные окружения (без файла)

#### fish shell (безопасный интерактивный ввод)

```fish
set -x SFU_USERNAME "ваш_логин"
read -s -P "Пароль еКурсов: " SFU_PASSWORD
set -x SFU_PASSWORD $SFU_PASSWORD
```

### bash / zsh

```bash
export SFU_USERNAME="ваш_логин"
read -rs -p "Пароль еКурсов: " SFU_PASSWORD; export SFU_PASSWORD
```

## Запуск

```bash
cd /home/pozumal/Projects/MCP
venv/bin/python server.py
```

Запускается MCP-сервер на транспорте `stdio`. В stdout идёт только протокол;
диагностика — в stderr. Проверить, что сервер поднимается:

```bash
venv/bin/python -c "import server; print('OK')"
```

## Подключение к MCP-клиенту

### opencode (`~/.config/opencode/opencode.jsonc`)

Актуальная схема (V2) хранит серверы в `mcp.servers`:

```jsonc
{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    "servers": {
      "sfu": {
        "type": "local",
        "command": [
          "/home/pozumal/Projects/MCP/venv/bin/python",
          "/home/pozumal/Projects/MCP/server.py"
        ]
      }
    }
  }
}
```

Учётные данные здесь указывать не нужно: сервер сам читает `.env` из своей
папки. Если клиент не наследует окружение и `.env` недоступен, добавьте
переменные явно:

```jsonc
        "environment": {
          "SFU_USERNAME": "ваш_логин",
          "SFU_PASSWORD": "ваш_пароль"
        }
```

Отключить сервер, не удаляя его, можно полем `"disabled": true` в блоке
сервера (в V2 вместо `enabled`).

Через CLI:

```bash
opencode mcp add sfu --global -- \
  /home/pozumal/Projects/MCP/venv/bin/python \
  /home/pozumal/Projects/MCP/server.py
```

После подключения инструменты вызываются как `tools.sfu.get_courses`,
`tools.sfu.get_grades`, `tools.sfu.get_assignments`,
`tools.sfu.get_upcoming_deadlines`, `tools.sfu.get_assignment_details`.

### Claude Desktop (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "sfu-ecourses": {
      "command": "/home/pozumal/Projects/MCP/venv/bin/python",
      "args": ["/home/pozumal/Projects/MCP/server.py"],
      "env": {
        "SFU_USERNAME": "ваш_логин",
        "SFU_PASSWORD": "ваш_пароль"
      }
    }
  }
}
```

> Хранение пароля в конфиге клиента — компромисс между удобством и
> безопасностью. Если клиент умеет наследовать окружение, оставьте учётные
> данные только в переменных окружения.

## Примеры запросов

- «Какие предметы у меня есть на еКурсах?» → `get_courses`
- «Покажи оценки по информационно-вычислительным сетям» → `get_grades`
- «Какие работы имеют дедлайн в ближайшие 7 дней?» → `get_upcoming_deadlines`
- «Что я уже просрочил?» → `get_upcoming_deadlines` (`overdue`)
- «Какие работы отправлены, но ещё не оценены?» → `get_assignments` (статус `submitted`)

## Тесты

Офлайн-тесты работают на синтетических фикстурах и не обращаются к сайту:

```bash
venv/bin/python -m unittest discover -s tests -t . -v
```

Если установлен `pytest`:

```bash
venv/bin/pip install pytest
venv/bin/pytest -q
```

## Live smoke-тест (ручной)

Обращается к реальному сайту и требует заданных переменных окружения:

```bash
SFU_USERNAME=... SFU_PASSWORD=... venv/bin/python smoke.py
# или для конкретного курса:
venv/bin/python smoke.py --course-id 41121
```

Секреты не печатаются. Live-проверка всех курсов не гарантируется при
отсутствии доступа к сайту.

## Диагностика

### DNS (`socket.gaierror: [Errno -2] Name or service not known`)

Проблема уровня ОС/сети, а не MCP. Проверьте разрешение имени:

```bash
getent hosts e.sfu-kras.ru
```

При необходимости добавьте корректную запись в `/etc/hosts`, используя
актуальный IP из DevTools браузера (не хардкодьте IP из примеров).

### TLS

Проверка сертификата всегда включена, IP не хардкодится. Если TLS не проходит,
исправьте системные корневые сертификаты, а не отключайте проверку.

### Авторизация

- Проверьте `SFU_USERNAME` / `SFU_PASSWORD`.
- Если появилась CAPTCHA, MFA или rate limit — сервер не обходит их и сообщает
  об ошибке входа. Войдите через браузер и разберитесь с ограничением.

## Безопасность

- Только чтение: сервер не отправляет, не редактирует и не скачивает работы.
- Пароль, cookies, `sesskey` и `logintoken` не логируются и не возвращаются клиенту.
- Запросы идут только на `e.sfu-kras.ru`, TLS включён, проверяются редиректы.
- `.env`, сессии и `venv` исключены через `.gitignore`.

## Ограничения и трактовка данных

- **Источник данных**: таблица обзора курса разбирается по атрибутам темы СФУ
  `data-mdl-overview-*` (`module_id` берётся из `data-mdl-overview-cmid`). Если
  атрибутов нет, включается запасной разбор по заголовкам столбцов; при этом
  отсутствующий столбец остаётся `null` и не подменяется соседним.
- **Время** дедлайнов берётся из абсолютного Unix-таймстампа сайта
  (`data-mdl-overview-item="duedate"`) и переводится в локальный часовой пояс
  машины — год не угадывается. Если таймстампа нет, дата разбирается из текста,
  и при неоднозначности (`due_at: null`) причина указывается в `due_warnings`
  (`year_missing`, `year_ambiguous`, `due_time_missing` и т.п.).
  Часовой пояс машины должен совпадать с поясом сайта, иначе настенное время
  сдвинется.
- **Просроченные** работы по умолчанию не попадают в `items`, а лежат в
  `overdue`; `include_overdue: true` переносит их в `items`.
- Задание без колонки срока сдачи — это отсутствие дедлайна, а не ошибка
  разбора: `due_date_raw: null`, `due_warnings: []`, в `undated` не попадает.
- **Статус сдачи**: пустое значение остаётся `unknown`, а не «не сдано».
- **Оценки**: `-` и пустые ячейки — это `null`, а не `0`. Текстовые оценки
  (например, «не зачтено») сохраняются в `grade_raw`, числовая оценка — в
  `grade_numeric` (для «отлично (100)» это `100`).
- Итоговые строки (`Итог в категории …`, `Итоговая оценка за курс`,
  `Рекомендованная оценка`) помечаются `is_total: true` и не суммируются вручную.
- Ошибка одного курса не прерывает общий отчёт по дедлайнам: она попадает в
  `course_errors`.
