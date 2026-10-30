"""Демо-данные: настоящий список лагерей и вымышленные участники и сотрудники.

Персонажи p001–p008 нужны для сценария проверки (см. README):
- p001–p003 уже подали заявки, p004 не подал — его заявка замыкает цепочку
  Кижи → Валдайский → Забайкальский → Пасвик из четырёх человек;
- p005 и p006 образуют парный обмен, он предлагается сразу при запуске;
- p007 и p008 — парный обмен, уже подтверждённый обоими и ждущий решения фонда.
Остальные участники и заявки генерируются случайно (с фиксированным seed)
и не затрагивают лагеря демо-персонажей, чтобы сценарий был предсказуемым.
"""
from __future__ import annotations

import csv
import random
from datetime import date
from pathlib import Path

from .services import respond, run_matching, save_request
from .store import APPROVER, CAMP_LEADER, COORDINATOR, CURATOR, Camp, Participant, Season, Staff, Store

DATA = Path(__file__).resolve().parent.parent / "data" / "camps.csv"

DEMO_PARTICIPANTS = [
    # id, имя, город, вуз, лагерь, желания (None — заявки нет)
    ("p001", "Анна Лебедева", "Петрозаводск", "ПетрГУ", 8, [7, 25]),
    ("p002", "Борис Ковалёв", "Великий Новгород", "НовГУ", 7, [3, 15]),
    ("p003", "Виктория Зайцева", "Улан-Удэ", "БГУ", 3, [13, 31]),
    ("p004", "Григорий Павлов", "Мурманск", "МАУ", 13, None),
    ("p005", "Дмитрий Соколов", "Красноярск", "СФУ", 20, [28]),
    ("p006", "Евгения Морозова", "Владивосток", "ДВФУ", 28, [20, 12]),
    ("p007", "Захар Орлов", "Санкт-Петербург", "СПбГУ", 14, [32]),
    ("p008", "Ирина Козлова", "Сочи", "СГУ", 32, [14]),
]
RESERVED_CAMPS = {3, 7, 8, 13, 20, 28, 14, 32}

STAFF = [
    ("approver1", "Марина Волкова", APPROVER, []),
    ("curator1", "Олег Тарасов", CURATOR, list(range(1, 32))),
    ("curator2", "Елена Крылова", CURATOR, list(range(32, 64))),
    ("leader1", "Дарья Кузнецова", CAMP_LEADER, [7, 8]),
    ("coord1", "Николай Сергеев", COORDINATOR, [8, 62]),
]

MALE = ["Алексей", "Андрей", "Артём", "Глеб", "Даниил", "Егор", "Иван", "Илья", "Кирилл", "Лев",
        "Максим", "Матвей", "Михаил", "Никита", "Павел", "Роман", "Семён", "Степан", "Тимофей", "Ярослав"]
FEMALE = ["Алина", "Анастасия", "Варвара", "Вера", "Дарья", "Екатерина", "Елизавета", "Ксения", "Маргарита",
          "Мария", "Надежда", "Полина", "София", "Таисия", "Ульяна", "Юлия", "Яна", "Алёна", "Милана", "Ева"]
SURNAMES = ["Андреев", "Белов", "Васильев", "Григорьев", "Данилов", "Егоров", "Жуков", "Зуев", "Ильин",
            "Карпов", "Лосев", "Медведев", "Никитин", "Осипов", "Поляков", "Рябов", "Савин", "Тихонов",
            "Устинов", "Фомин", "Харитонов", "Чернов", "Шилов", "Щукин", "Яковлев", "Гусев", "Комаров",
            "Титов", "Баранов", "Широков"]
CITIES = [("Москва", "МГУ"), ("Санкт-Петербург", "СПбГУ"), ("Казань", "КФУ"), ("Екатеринбург", "УрФУ"),
          ("Новосибирск", "НГУ"), ("Томск", "ТГУ"), ("Нижний Новгород", "ННГУ"), ("Самара", "Самарский университет"),
          ("Пермь", "ПГНИУ"), ("Иркутск", "ИГУ"), ("Ростов-на-Дону", "ЮФУ"), ("Воронеж", "ВГУ"),
          ("Тюмень", "ТюмГУ"), ("Уфа", "УУНиТ"), ("Калининград", "БФУ"), ("Москва", "НИУ ВШЭ")]
PER_CAMP = 4
RANDOM_REQUESTS = 60


def load_camps() -> list[Camp]:
    with DATA.open(encoding="utf-8") as f:
        return [
            Camp(int(r["id"]), r["name"], r["region"], date.fromisoformat(r["start_date"]), date.fromisoformat(r["end_date"]))
            for r in csv.DictReader(f)
        ]


def seed(store: Store) -> None:
    rng = random.Random(2027)
    store.clear()
    store.season = Season(1, "Школа Фонда Потанина 2027 (демо)", date(2026, 10, 1), date(2027, 5, 31))
    store.camps = {c.id: c for c in load_camps()}
    for sid, name, role, camps in STAFF:
        store.staff[sid] = Staff(sid, name, f"{sid}@example.org", role, camps)

    def add(pid: str, name: str, city: str, university: str, camp_id: int) -> Participant:
        p = Participant(pid, name, f"{pid}@example.org", f"id_demo_{pid}" if rng.random() < 0.7 else None,
                        city, university, date(rng.randint(2001, 2006), rng.randint(1, 12), rng.randint(1, 28)), camp_id)
        store.participants[pid] = p
        return p

    for pid, name, city, university, camp_id, _ in DEMO_PARTICIPANTS:
        add(pid, name, city, university, camp_id)

    n = len(DEMO_PARTICIPANTS)
    for camp_id in store.camps:
        for _ in range(PER_CAMP):
            n += 1
            female = rng.random() < 0.6
            name = f"{rng.choice(FEMALE if female else MALE)} {rng.choice(SURNAMES)}{'а' if female else ''}"
            city, university = rng.choice(CITIES)
            add(f"p{n:03d}", name, city, university, camp_id)

    for pid, *_, wishes in DEMO_PARTICIPANTS:
        if wishes:
            save_request(store.participants[pid], wishes)

    open_camps = [c for c in store.camps if c not in RESERVED_CAMPS]
    candidates = [p for p in store.participants.values() if p.id > "p008" and p.camp_id not in RESERVED_CAMPS]
    for p in rng.sample(candidates, RANDOM_REQUESTS):
        options = [c for c in open_camps if c != p.camp_id]
        save_request(p, rng.sample(options, rng.randint(1, 5)))

    run_matching("seed")
    for swap in store.swaps.values():
        if swap.member("p007"):
            respond(swap.id, store.participants["p007"], "accepted")
            respond(swap.id, store.participants["p008"], "accepted")
