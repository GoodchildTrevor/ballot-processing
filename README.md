# ballot-processing

Веб-приложение для голосования на кинопремии.

## Стек
- FastAPI + SQLAlchemy (sync) + SQLite
- Jinja2 templates
- Tailwind CSS (CDN) + Alpine.js (CDN)
- openpyxl для экспорта .xlsx

## Запуск

```bash
pip install -r requirements.txt

# Задать логин/пароль для админки (или оставить дефолтные admin/secret)
export ADMIN_USER=admin
export ADMIN_PASS=secret

uvicorn ballot.main:app --reload
```

## Структура

```
ballot/
├── main.py
├── database.py
├── models.py
├── auth.py
├── routers/
│   ├── vote.py
│   ├── admin_films.py
│   ├── admin_nominations.py
│   ├── admin_voters.py
│   └── admin_results.py
└── templates/
    ├── base.html
    ├── index.html
    ├── vote.html
    ├── thankyou.html
    └── admin/
        ├── films.html
        ├── film_detail.html
        ├── nominations.html
        ├── nomination_detail.html
        ├── voters.html
        └── results.html
```

## Схема БД

| Таблица | Поля |
|---|---|
| Film | id, title, year |
| Person | id, name |
| Nomination | id, name, type (RANK/PICK) |
| Nominee | id, nomination_id, film_id, person_id? |
| Voter | id, name UNIQUE, voted_at? |
| Vote | id, voter_id, nominee_id |
| Ranking | id, voter_id, nomination_id, film_id, rank |

## Роуты

### Публичные
| Метод | Путь | Описание |
|---|---|---|
| GET | `/` | Форма ввода ника |
| POST | `/` | Вход / создание voter |
| GET | `/vote/{id}` | Бюллетень |
| POST | `/vote/{id}` | Отправить голос |

### Админка (Basic Auth)
| Метод | Путь | Описание |
|---|---|---|
| GET/POST | `/admin/films` | Список + создание фильмов |
| GET | `/admin/films/{id}` | Карточка фильма |
| POST | `/admin/films/{id}/nominees` | Добавить номинанта |
| POST | `/admin/nominees/{id}/delete` | Удалить номинанта |
| GET/POST | `/admin/nominations` | Список + создание номинаций |
| GET | `/admin/nominations/{id}` | Детали номинации |
| GET | `/admin/voters` | Список участников |
| GET | `/admin/results` | Результаты |
| GET | `/admin/results/export` | Скачать .xlsx |
