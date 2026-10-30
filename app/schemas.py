"""Схемы запросов и ответов API (из них FastAPI строит OpenAPI-спецификацию)."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field

RequestStatus = Literal["active", "in_swap", "fulfilled", "withdrawn"]
SwapStatus = Literal["proposed", "confirmed", "approved", "declined", "expired", "rejected"]
Role = Literal["participant", "camp_leader", "coordinator", "curator", "approver"]


class Error(BaseModel):
    detail: str = Field(description="Описание ошибки на русском языке")


class Camp(BaseModel):
    id: int
    name: str
    region: str
    start_date: date
    end_date: date


class CampCatalogItem(Camp):
    participants: int = Field(description="Сколько участников сейчас распределено в лагерь")
    want_to_leave: int = Field(description="Сколько участников лагеря подали заявку на обмен")
    want_to_join: int = Field(description="В скольких активных заявках лагерь указан среди желаемых")


class Wish(BaseModel):
    rank: int = Field(description="Приоритет: 1 — самый желанный лагерь")
    camp: Camp


class RequestIn(BaseModel):
    wishes: list[int] = Field(
        min_length=1, max_length=5,
        description="id желаемых лагерей в порядке приоритета, от 1 до 5, без своего лагеря",
    )


class RequestOut(BaseModel):
    participant_id: str
    status: RequestStatus
    wishes: list[Wish]
    created_at: datetime
    updated_at: datetime
    active_swap_id: int | None = Field(description="Обмен, в который сейчас входит заявка")


class Me(BaseModel):
    id: str
    full_name: str
    role: Role
    camp: Camp | None = Field(None, description="Текущий лагерь (для участника)")
    request: RequestOut | None = Field(None, description="Заявка на обмен (для участника)")
    active_swap_id: int | None = None
    camps: list[Camp] = Field(default_factory=list, description="Закреплённые лагеря (для сотрудников)")


class Contacts(BaseModel):
    email: str
    vk_id: str | None


class SwapMember(BaseModel):
    participant_id: str
    full_name: str
    city: str
    from_camp: Camp
    to_camp: Camp
    wish_rank: int | None = Field(description="Каким по приоритету желанием участника является новый лагерь")
    response: Literal["pending", "accepted", "declined"]
    responded_at: datetime | None
    contacts: Contacts | None = Field(description="Открываются участникам после подтверждения обмена всеми")


class HistoryItem(BaseModel):
    at: datetime
    actor: str
    action: str
    details: str


class Swap(BaseModel):
    id: int
    source: Literal["auto", "manual"] = Field(description="auto — найден сервисом подбора, manual — оформлен участниками")
    status: SwapStatus
    created_at: datetime
    expires_at: datetime = Field(description="Срок ответа участников")
    seconds_left: int | None = Field(description="Сколько секунд осталось на ответ (для статуса proposed)")
    members: list[SwapMember]
    decided_by: str | None
    decided_at: datetime | None
    decision_comment: str | None
    history: list[HistoryItem] = Field(description="Журнал действий по обмену")


class MoveIn(BaseModel):
    participant_id: str
    to_camp_id: int


class ManualSwapIn(BaseModel):
    members: list[MoveIn] = Field(
        min_length=2, max_length=4,
        description="Кто в какой лагерь переходит; инициатор (X-User-Id) должен быть в списке",
    )


class ResponseIn(BaseModel):
    response: Literal["accepted", "declined"]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(None, max_length=1000, description="Обязателен при отклонении")


class RosterItem(BaseModel):
    participant_id: str
    full_name: str
    city: str | None = Field(None, description="Кураторам, согласующим и координаторам")
    birth_date: date | None = Field(None, description="Координатору — для оформления пропусков")
    email: str | None = Field(None, description="Кэмп-лидеру — для связи с группой")
    vk_id: str | None = Field(None, description="Кэмп-лидеру — для связи с группой")
    arrived_by_swap_id: int | None = Field(description="Если участник попал в лагерь по обмену — номер обмена")


class Roster(BaseModel):
    camp: Camp
    participants: list[RosterItem]


class MatchingRun(BaseModel):
    trigger: str
    started_at: datetime
    expired_swap_ids: list[int] = Field(description="Предложения, снятые по истечении срока ответа")
    active_requests: int
    graph_edges: int
    cycles_found: int = Field(description="Найдено допустимых циклов из 2–4 участников")
    proposed_swap_ids: list[int] = Field(description="Созданные предложения обменов")
    skipped_reason: str | None
    duration_ms: float


class NotificationOut(BaseModel):
    id: int
    channel: Literal["vk", "email"]
    event: str
    text: str
    created_at: datetime
