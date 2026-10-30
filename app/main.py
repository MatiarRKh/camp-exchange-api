"""REST API биржи обмена лагерями (демо-версия API-сервиса из концепции)."""
import asyncio
import csv
import io
import os
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Body, Depends, FastAPI, Header, HTTPException, Path, Query, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse, RedirectResponse

from . import schemas, services
from .seed import seed
from .store import (
    APPROVER, CAMP_LEADER, CONFIRMED, COORDINATOR, CURATOR, PROPOSED, REQUEST_ACTIVE, REQUEST_IN_SWAP,
    Participant, Staff, Swap, store,
)

MATCHING_INTERVAL_SEC = int(os.getenv("MATCHING_INTERVAL_SEC", "600"))

DEMO_USERS = {
    "p004": "Григорий Павлов, участник (Пасвик), заявки ещё нет",
    "p001": "Анна Лебедева, участница (Кижи)",
    "p002": "Борис Ковалёв, участник (Валдайский)",
    "p003": "Виктория Зайцева, участница (Забайкальский)",
    "p005": "Дмитрий Соколов, участник (Красноярские Столбы)",
    "p006": "Евгения Морозова, участница (Земля леопарда)",
    "approver1": "Марина Волкова, согласующий фонда",
    "curator1": "Олег Тарасов, куратор лагерей 1–31",
    "leader1": "Дарья Кузнецова, кэмп-лидер (Валдайский, Кижи)",
    "coord1": "Николай Сергеев, координатор (Кижи)",
}

DESCRIPTION = """
Демо-версия API-сервиса **биржи обмена лагерями** для участников Школы Фонда Потанина
(учебный проект НИУ ВШЭ, не официальный сервис фонда).

Лагеря — настоящий список сезона 2026 (даты перенесены на 2027 год), участники и сотрудники вымышленные.
Данные хранятся в памяти; `POST /api/v1/demo/reset` возвращает их в исходное состояние.

**Авторизация (заглушка).** В полной системе пользователь входит по одноразовому коду и получает токен.
Здесь пользователь передаётся заголовком `X-User-Id` — в каждом запросе его можно выбрать из списка:

| X-User-Id | Кто это |
|---|---|
""" + "\n".join(f"| `{uid}` | {label} |" for uid, label in DEMO_USERS.items()) + """

**Сценарий проверки — обмен по цепочке из четырёх человек:**
1. `PUT /api/v1/me/request` от `p004` с телом `{"wishes": [8, 17]}` — Гриша хочет в Кижи. Подбор запускается сразу и находит цепочку
   Кижи → Валдайский → Забайкальский → Пасвик (`active_swap_id` в ответе; после сброса стенда это обмен №14).
2. `PATCH /api/v1/swaps/{id}/members/me` с `{"response": "accepted"}` от `p001`, `p002`, `p003`, `p004` — обмен переходит в `confirmed`.
3. `POST /api/v1/swaps/{id}/decision` от `approver1` с `{"decision": "approve"}` — лагеря меняются у всех четверых сразу.
4. `GET /api/v1/camps/8/roster` от `leader1` или `coord1` — в Кижи теперь Григорий; `GET /api/v1/me/notifications` — какие уведомления ушли.

Обмен №1 (p005 ↔ p006) с самого начала ждёт ответа участников, обмен №2 (p007 ↔ p008) — решения фонда.
"""

TAGS = [
    {"name": "Лагеря", "description": "Каталог лагерей и составы для команд лагерей"},
    {"name": "Участник", "description": "Профиль, заявка на обмен, уведомления"},
    {"name": "Обмены", "description": "Предложения обменов и ответы участников"},
    {"name": "Фонд", "description": "Согласование, выгрузка, ручной запуск подбора"},
    {"name": "Демо", "description": "Служебные методы демо-стенда"},
]

ERRORS = {
    401: {"model": schemas.Error, "description": "Пользователь не передан или неизвестен"},
    403: {"model": schemas.Error, "description": "Недостаточно прав"},
    404: {"model": schemas.Error, "description": "Объект не найден"},
    409: {"model": schemas.Error, "description": "Конфликт с текущим состоянием"},
}


async def scheduler() -> None:
    """Обработчик по расписанию: снимает просроченные предложения и ищет новые обмены."""
    def tick() -> None:
        with store.lock:
            services.run_matching("schedule")

    while True:
        await asyncio.sleep(MATCHING_INTERVAL_SEC)
        await run_in_threadpool(tick)


@asynccontextmanager
async def lifespan(_: FastAPI):
    with store.lock:
        seed(store)
    task = asyncio.create_task(scheduler())
    yield
    task.cancel()


app = FastAPI(
    title="Биржа обмена лагерями — API",
    version="1.0.0",
    description=DESCRIPTION,
    openapi_tags=TAGS,
    lifespan=lifespan,
    swagger_ui_parameters={"tryItOutEnabled": True, "displayRequestDuration": True, "docExpansion": "list"},
)


@app.exception_handler(services.DomainError)
async def domain_error_handler(_: Request, exc: services.DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status, content={"detail": exc.detail})


# ---------- Авторизация (заглушка) ----------

def auth(*preferred: str, roles: set[str] | None = None):
    """Зависимость «текущий пользователь» с примерами X-User-Id для Swagger UI."""
    order = list(preferred) + [uid for uid in DEMO_USERS if uid not in preferred]
    examples = {uid: {"summary": f"{uid} — {DEMO_USERS[uid]}", "value": uid} for uid in order}

    def dependency(
        x_user_id: Annotated[str, Header(alias="X-User-Id", description="Демо-пользователь", openapi_examples=examples)],
    ) -> Participant | Staff:
        user = store.user(x_user_id)
        if user is None:
            raise HTTPException(401, "Неизвестный пользователь: список демо-пользователей — в описании API")
        if roles and user.role not in roles:
            raise HTTPException(403, "Метод недоступен для вашей роли")
        return user

    return dependency


def require_participant(user: Participant | Staff) -> Participant:
    if not isinstance(user, Participant):
        raise HTTPException(403, "Метод доступен только участникам")
    return user


# ---------- Преобразование в схемы ответа ----------

def camp_out(camp_id: int) -> schemas.Camp:
    c = store.camps[camp_id]
    return schemas.Camp(id=c.id, name=c.name, region=c.region, start_date=c.start_date, end_date=c.end_date)


def request_out(participant_id: str) -> schemas.RequestOut | None:
    r = store.requests.get(participant_id)
    if r is None:
        return None
    swap = store.active_swap_of(participant_id)
    return schemas.RequestOut(
        participant_id=r.participant_id, status=r.status,
        wishes=[schemas.Wish(rank=i, camp=camp_out(c)) for i, c in enumerate(r.wishes, start=1)],
        created_at=r.created_at, updated_at=r.updated_at, active_swap_id=swap.id if swap else None,
    )


def can_view_swap(user: Participant | Staff, swap: Swap) -> bool:
    if isinstance(user, Participant):
        return swap.member(user.id) is not None
    return user.role == APPROVER or bool(swap.camp_ids() & set(user.camp_ids))


def swap_out(swap: Swap, viewer: Participant | Staff) -> schemas.Swap:
    contacts_open = isinstance(viewer, Participant) and swap.status in (CONFIRMED, "approved")
    members = []
    for m in swap.members:
        p = store.participants[m.participant_id]
        members.append(schemas.SwapMember(
            participant_id=p.id, full_name=p.full_name, city=p.city,
            from_camp=camp_out(m.from_camp_id), to_camp=camp_out(m.to_camp_id), wish_rank=m.wish_rank,
            response=m.response, responded_at=m.responded_at,
            contacts=schemas.Contacts(email=p.email, vk_id=p.vk_id) if contacts_open else None,
        ))
    seconds_left = None
    if swap.status == PROPOSED:
        seconds_left = max(0, int((swap.expires_at - services.now()).total_seconds()))
    return schemas.Swap(
        id=swap.id, source=swap.source, status=swap.status, created_at=swap.created_at, expires_at=swap.expires_at,
        seconds_left=seconds_left, members=members, decided_by=swap.decided_by, decided_at=swap.decided_at,
        decision_comment=swap.decision_comment,
        history=[schemas.HistoryItem(at=e.at, actor=e.actor, action=e.action, details=e.details)
                 for e in store.audit if e.swap_id == swap.id],
    )


def visible_swap(swap_id: int, user: Participant | Staff) -> Swap:
    swap = services.get_swap(swap_id)
    if not can_view_swap(user, swap):
        raise HTTPException(403, "Нет доступа к этому обмену")
    return swap


# ---------- Лагеря ----------

@app.get("/", include_in_schema=False)
def root() -> RedirectResponse:
    return RedirectResponse("/docs")


@app.get("/api/v1/camps", tags=["Лагеря"], summary="Каталог лагерей сезона", response_model=list[schemas.CampCatalogItem])
def list_camps(
    q: Annotated[str | None, Query(description="Поиск по названию")] = None,
    region: Annotated[str | None, Query(description="Фильтр по региону, точное совпадение")] = None,
):
    """Лагеря текущего сезона со спросом: сколько участников хотят уйти и сколько — попасть в лагерь."""
    with store.lock:
        active = [r for r in store.requests.values() if r.status in (REQUEST_ACTIVE, REQUEST_IN_SWAP)]
        result = []
        for c in store.camps.values():
            if q and q.lower() not in c.name.lower():
                continue
            if region and region != c.region:
                continue
            result.append(schemas.CampCatalogItem(
                **camp_out(c.id).model_dump(),
                participants=sum(1 for p in store.participants.values() if p.camp_id == c.id),
                want_to_leave=sum(1 for r in active if store.participants[r.participant_id].camp_id == c.id),
                want_to_join=sum(1 for r in active if c.id in r.wishes),
            ))
        return result


@app.get("/api/v1/camps/{camp_id}/roster", tags=["Лагеря"], summary="Актуальный состав лагеря",
         response_model=schemas.Roster, responses=ERRORS)
def camp_roster(
    camp_id: Annotated[int, Path(description="id лагеря", examples=[8])],
    user: Annotated[Staff, Depends(auth("leader1", "coord1", "curator1", roles={CAMP_LEADER, COORDINATOR, CURATOR, APPROVER}))],
):
    """Состав лагеря для команды лагеря и сотрудников фонда. Набор полей зависит от роли:
    кэмп-лидеру — контакты для связи с группой, координатору — данные для пропусков."""
    with store.lock:
        if camp_id not in store.camps:
            raise HTTPException(404, "Лагерь не найден")
        if user.role != APPROVER and camp_id not in user.camp_ids:
            raise HTTPException(403, "Лагерь не закреплён за вами")
        items = []
        for p in sorted(store.participants.values(), key=lambda p: p.full_name):
            if p.camp_id != camp_id:
                continue
            item = schemas.RosterItem(participant_id=p.id, full_name=p.full_name, arrived_by_swap_id=p.arrived_by_swap_id)
            if user.role == CAMP_LEADER:
                item.email, item.vk_id = p.email, p.vk_id
            elif user.role == COORDINATOR:
                item.birth_date, item.city = p.birth_date, p.city
            else:
                item.city = p.city
            items.append(item)
        return schemas.Roster(camp=camp_out(camp_id), participants=items)


# ---------- Участник ----------

@app.get("/api/v1/me", tags=["Участник"], summary="Текущий пользователь", response_model=schemas.Me, responses=ERRORS)
def get_me(user: Annotated[Participant | Staff, Depends(auth("p004", "p001"))]):
    """Для участника — лагерь, заявка и активный обмен; для сотрудника — роль и закреплённые лагеря."""
    with store.lock:
        if isinstance(user, Participant):
            swap = store.active_swap_of(user.id)
            return schemas.Me(id=user.id, full_name=user.full_name, role=user.role, camp=camp_out(user.camp_id),
                              request=request_out(user.id), active_swap_id=swap.id if swap else None)
        return schemas.Me(id=user.id, full_name=user.full_name, role=user.role,
                          camps=[camp_out(c) for c in user.camp_ids])


@app.put(
    "/api/v1/me/request", tags=["Участник"], summary="Создать или изменить заявку на обмен",
    response_model=schemas.RequestOut, responses={**ERRORS, 201: {"model": schemas.RequestOut, "description": "Заявка создана"}},
)
def put_request(
    response: Response,
    user: Annotated[Participant | Staff, Depends(auth("p004"))],
    body: Annotated[schemas.RequestIn, Body(openapi_examples={
        "chain": {"summary": "Гриша (p004) хочет в Кижи — замыкает цепочку", "value": {"wishes": [8, 17]}},
        "several": {"summary": "Несколько лагерей по приоритету", "value": {"wishes": [12, 28, 54]}},
    })],
):
    """Участник указывает до 5 лагерей по приоритету. Заявка сразу участвует в подборе:
    после сохранения запускается сервис подбора (в полной системе — по событию `offer.changed` из брокера).
    Если обмен нашёлся, в ответе будет `status: in_swap` и `active_swap_id`."""
    participant = require_participant(user)
    with store.lock:
        _, created = services.save_request(participant, body.wishes)
        services.run_matching("offer.changed")
        response.status_code = 201 if created else 200
        return request_out(participant.id)


@app.delete("/api/v1/me/request", tags=["Участник"], summary="Отозвать заявку", status_code=204, responses=ERRORS)
def delete_request(user: Annotated[Participant | Staff, Depends(auth("p001"))]):
    """Заявку можно отозвать, пока она не входит в предложенный обмен."""
    participant = require_participant(user)
    with store.lock:
        services.withdraw_request(participant)
    return Response(status_code=204)


@app.get("/api/v1/me/notifications", tags=["Участник"], summary="Мои уведомления",
         response_model=list[schemas.NotificationOut], responses=ERRORS)
def my_notifications(
    user: Annotated[Participant | Staff, Depends(auth("p004", "leader1", "approver1"))],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
):
    """Что сервис уведомлений отправил пользователю во ВКонтакте или на email (в демо — журнал отправки)."""
    with store.lock:
        items = [n for n in reversed(store.notifications) if n.user_id == user.id][:limit]
        return [schemas.NotificationOut(id=n.id, channel=n.channel, event=n.event, text=n.text, created_at=n.created_at)
                for n in items]


# ---------- Обмены ----------

@app.get("/api/v1/swaps", tags=["Обмены"], summary="Список обменов", response_model=list[schemas.Swap], responses=ERRORS)
def list_swaps(
    user: Annotated[Participant | Staff, Depends(auth("approver1", "p001", "curator1"))],
    status: Annotated[schemas.SwapStatus | None, Query(description="Фильтр по статусу; очередь фонда — confirmed")] = None,
):
    """Участник видит свои обмены, согласующий — все, кураторы и команды лагерей — обмены своих лагерей."""
    with store.lock:
        swaps = [s for s in store.swaps.values() if can_view_swap(user, s) and (status is None or s.status == status)]
        return [swap_out(s, user) for s in sorted(swaps, key=lambda s: s.id, reverse=True)]


@app.post("/api/v1/swaps", tags=["Обмены"], summary="Оформить обмен, о котором участники договорились сами",
          status_code=201, response_model=schemas.Swap, responses=ERRORS)
def create_manual_swap(
    user: Annotated[Participant | Staff, Depends(auth("p001"))],
    body: Annotated[schemas.ManualSwapIn, Body(openapi_examples={
        "pair": {"summary": "Анна (Кижи) и Борис (Валдайский) меняются", "value": {"members": [
            {"participant_id": "p001", "to_camp_id": 7}, {"participant_id": "p002", "to_camp_id": 8}]}},
    })],
):
    """Инициатор указывает, кто в какой лагерь переходит. Обмен должен быть замкнут:
    каждый лагерь отдаётся и получается ровно один раз. Остальные участники получают приглашение
    и подтверждают его так же, как найденный автоматически."""
    participant = require_participant(user)
    with store.lock:
        swap = services.propose_manual(participant, [(m.participant_id, m.to_camp_id) for m in body.members])
        return swap_out(swap, participant)


@app.get("/api/v1/swaps/{swap_id}", tags=["Обмены"], summary="Карточка обмена", response_model=schemas.Swap, responses=ERRORS)
def get_swap(
    swap_id: Annotated[int, Path(examples=[1])],
    user: Annotated[Participant | Staff, Depends(auth("p001", "approver1"))],
):
    """Кто куда переходит, каким по счёту желанием это является, ответы участников, срок ответа и журнал действий."""
    with store.lock:
        return swap_out(visible_swap(swap_id, user), user)


@app.patch("/api/v1/swaps/{swap_id}/members/me", tags=["Обмены"], summary="Ответить на предложение обмена",
           response_model=schemas.Swap, responses=ERRORS)
def respond_to_swap(
    swap_id: Annotated[int, Path(examples=[1])],
    user: Annotated[Participant | Staff, Depends(auth("p001", "p002", "p003", "p004"))],
    body: Annotated[schemas.ResponseIn, Body(openapi_examples={
        "accept": {"summary": "Согласен", "value": {"response": "accepted"}},
        "decline": {"summary": "Отказываюсь", "value": {"response": "declined"}},
    })],
):
    """Когда согласны все, обмен переходит в `confirmed`, участникам открываются контакты друг друга,
    а обмен попадает в очередь фонда. Отказ одного отменяет обмен для всех: остальные возвращаются
    в подбор, а эта комбинация больше не предлагается."""
    participant = require_participant(user)
    with store.lock:
        swap = services.respond(swap_id, participant, body.response)
        if swap.status != PROPOSED and swap.status != CONFIRMED:
            services.run_matching("swap.closed")
        return swap_out(swap, participant)


# ---------- Фонд ----------

@app.post("/api/v1/swaps/{swap_id}/decision", tags=["Фонд"], summary="Согласовать или отклонить обмен",
          response_model=schemas.Swap, responses={**ERRORS, 422: {"model": schemas.Error, "description": "Отклонение без комментария"}})
def decide_swap(
    swap_id: Annotated[int, Path(examples=[2])],
    user: Annotated[Staff, Depends(auth("approver1", roles={APPROVER}))],
    body: Annotated[schemas.DecisionIn, Body(openapi_examples={
        "approve": {"summary": "Согласовать", "value": {"decision": "approve"}},
        "reject": {"summary": "Отклонить с комментарием", "value": {"decision": "reject", "comment": "Билеты уже выкуплены фондом"}},
    })],
):
    """Только для согласующего и только для обменов в статусе `confirmed`. При согласовании лагеря меняются
    у всех участников сразу (либо у всех, либо ни у кого), команды затронутых лагерей получают уведомление.
    Отклонить без комментария нельзя."""
    with store.lock:
        swap = services.decide(swap_id, user, body.decision, body.comment)
        if swap.status != "approved":
            services.run_matching("swap.closed")
        return swap_out(swap, user)


@app.get("/api/v1/exports/approved-swaps.csv", tags=["Фонд"], summary="Выгрузка согласованных обменов",
         response_class=Response, responses={200: {"content": {"text/csv": {}}, "description": "CSV, разделитель «;»"}, **ERRORS})
def export_approved(user: Annotated[Staff, Depends(auth("approver1", "curator1", roles={APPROVER, CURATOR}))]):
    """Таблица для подготовки документов: кто, из какого лагеря, в какой, когда согласовано."""
    with store.lock:
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";")
        writer.writerow(["swap_id", "approved_at", "participant_id", "full_name", "from_camp", "to_camp"])
        for swap in sorted(store.swaps.values(), key=lambda s: s.id):
            if swap.status != "approved" or not can_view_swap(user, swap):
                continue
            for m in swap.members:
                writer.writerow([swap.id, f"{swap.decided_at:%Y-%m-%d %H:%M}", m.participant_id,
                                 store.participants[m.participant_id].full_name,
                                 store.camps[m.from_camp_id].name, store.camps[m.to_camp_id].name])
    return Response("﻿" + buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": "attachment; filename=approved-swaps.csv"})


@app.post("/api/v1/matching/runs", tags=["Фонд"], summary="Запустить подбор вручную",
          response_model=schemas.MatchingRun, responses=ERRORS)
def run_matching(user: Annotated[Staff, Depends(auth("curator1", "approver1", roles={CURATOR, APPROVER}))]):
    """Тот же обработчик, что работает по расписанию: снимает просроченные предложения,
    строит граф желаний, ищет циклы из 2–4 участников и создаёт предложения."""
    with store.lock:
        return services.run_matching(f"manual:{user.id}")


# ---------- Демо ----------

@app.post("/api/v1/demo/reset", tags=["Демо"], summary="Сбросить демо-данные", status_code=204)
def reset_demo():
    """Возвращает стенд в исходное состояние, чтобы пройти сценарий проверки заново."""
    with store.lock:
        seed(store)
    return Response(status_code=204)
