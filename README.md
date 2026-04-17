# ballot-processing

Небольшое веб-приложение для проведения голосования (бюллетеней) на кинопремии.

Технологии
- FastAPI
- SQLAlchemy (синхронный)
- Jinja2 шаблоны
- Tailwind CSS (CDN), Alpine.js (CDN)
- SQLite по умолчанию
- openpyxl для экспорта результатов в .xlsx
- Миграции: Alembic

Быстрый запуск (локально)
1. Установить зависимости:
```bash
pip install -r requirements.txt
```

2. Создать файл окружения (или задать переменные):
```bash
cp .env.example .env
# либо
export ADMIN_USER=admin
export ADMIN_PASS=secret
# (опционально) субадмин — может добавлять фильмы/персон/номинантов, но не видит результаты и не управляет конкурсами
export SUBADMIN_USER=subadmin
export SUBADMIN_PASS=subsecret
```

3. Применить миграции БД:
```bash
alembic upgrade head
```

4. Запустить приложение:
```bash
uvicorn ballot.main:app --reload
```

Запуск в Docker
- При сборке образа скрипт entrypoint.sh будет запускать миграции Alembic автоматически перед стартом приложения.
```bash
docker-compose up --build
```

Если нужно собрать и запустить вручную:
```bash
docker build -t ballot-processing .
docker run --env-file .env -p 8000:8000 ballot-processing
```

Структура проекта (кратко)
- ballot/ — код приложения
  - main.py — точка входа
  - database.py — подключение и инициализация БД
  - models.py — ORM-модели
  - auth.py — простая BasicAuth для админки
  - routers/ — маршруты приложения (public + admin)
  - templates/ — Jinja2 шаблоны
- alembic/ — миграции БД
- requirements.txt, Dockerfile, docker-compose.yml

Краткое описание БД (основные сущности)
- Film: id, title, year
- Person: id, name
- Nomination: id, name, type (RANK / PICK)
- Nominee: id, nomination_id, film_id, person_id?
- Voter: id, name (unique), voted_at
- Vote / Ranking: связи голосов и ранжирования по номинациям

Основные роуты
Публичные
- GET / — форма ввода ника
- POST / — вход / создание voter
- GET /vote/{id} — страница с бюллетенём
- POST /vote/{id} — отправка голосов

Админка (Basic Auth)
- GET/POST /admin/films — список фильмов + создание
- GET /admin/films/{id} — карточка фильма
- POST /admin/films/{id}/nominees — добавить номинанта
- POST /admin/nominees/{id}/delete — удалить номинанта
- GET/POST /admin/nominations — список/создание номинаций
- GET /admin/nominations/{id} — детали номинации
- GET /admin/voters — список участников
- GET /admin/results — результаты
- GET /admin/results/export — скачать .xlsx

Советы и замечания
- По умолчанию используется SQLite (файл в проекте).
- Админские креденшелы берутся из окружения (ADMIN_USER, ADMIN_PASS) или .env.
- Перед развёртыванием убедитесь, что применили миграции alembic.
