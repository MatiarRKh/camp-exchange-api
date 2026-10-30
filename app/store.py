"""Хранилище в памяти.

В демо-версии заменяет PostgreSQL: здесь те же сущности, что и в ER-диаграмме
(docs/diagrams/er.puml), а блокировка store.lock играет роль транзакции.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import date, datetime

PARTICIPANT = "participant"
CAMP_LEADER = "camp_leader"
COORDINATOR = "coordinator"
CURATOR = "curator"
APPROVER = "approver"

# Статусы заявки
REQUEST_ACTIVE = "active"        # участвует в подборе
REQUEST_IN_SWAP = "in_swap"      # входит в активное предложение обмена
REQUEST_FULFILLED = "fulfilled"  # обмен согласован, лагерь сменён
REQUEST_WITHDRAWN = "withdrawn"  # отозвана участником

# Статусы обмена
PROPOSED = "proposed"    # предложен, ждёт ответа участников
CONFIRMED = "confirmed"  # подтверждён всеми участниками, ждёт решения фонда
APPROVED = "approved"    # согласован фондом и применён
DECLINED = "declined"    # отклонён участником
EXPIRED = "expired"      # кто-то не ответил за отведённое время
REJECTED = "rejected"    # отклонён фондом
ACTIVE_SWAP_STATUSES = {PROPOSED, CONFIRMED}


@dataclass
class Season:
    id: int
    title: str
    exchange_opens: date
    exchange_closes: date


@dataclass
class Camp:
    id: int
    name: str
    region: str
    start_date: date
    end_date: date


@dataclass
class Participant:
    id: str
    full_name: str
    email: str
    vk_id: str | None
    city: str
    university: str
    birth_date: date
    camp_id: int
    arrived_by_swap_id: int | None = None
    role: str = PARTICIPANT


@dataclass
class Staff:
    id: str
    full_name: str
    email: str
    role: str
    camp_ids: list[int]


@dataclass
class ExchangeRequest:
    participant_id: str
    wishes: list[int]
    status: str
    created_at: datetime
    updated_at: datetime


@dataclass
class SwapMember:
    participant_id: str
    from_camp_id: int
    to_camp_id: int
    wish_rank: int | None
    response: str = "pending"  # pending | accepted | declined
    responded_at: datetime | None = None


@dataclass
class Swap:
    id: int
    source: str  # auto — найден сервисом подбора, manual — оформлен участниками
    status: str
    members: list[SwapMember]
    created_at: datetime
    expires_at: datetime
    created_by: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    decision_comment: str | None = None

    def member(self, participant_id: str) -> SwapMember | None:
        return next((m for m in self.members if m.participant_id == participant_id), None)

    def combination(self) -> frozenset[tuple[str, int]]:
        """Кто в какой лагерь переходит — по этому ключу отклонённые обмены не предлагаются повторно."""
        return frozenset((m.participant_id, m.to_camp_id) for m in self.members)

    def camp_ids(self) -> set[int]:
        return {m.from_camp_id for m in self.members} | {m.to_camp_id for m in self.members}


@dataclass
class Notification:
    id: int
    user_id: str
    channel: str  # vk | email
    event: str
    text: str
    created_at: datetime


@dataclass
class AuditEntry:
    at: datetime
    actor: str
    action: str
    swap_id: int | None
    details: str


class Store:
    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.clear()

    def clear(self) -> None:
        self.season: Season | None = None
        self.camps: dict[int, Camp] = {}
        self.participants: dict[str, Participant] = {}
        self.staff: dict[str, Staff] = {}
        self.requests: dict[str, ExchangeRequest] = {}
        self.swaps: dict[int, Swap] = {}
        self.banned: set[frozenset[tuple[str, int]]] = set()
        self.notifications: list[Notification] = []
        self.audit: list[AuditEntry] = []
        self._swap_seq = 0
        self._notification_seq = 0

    def next_swap_id(self) -> int:
        self._swap_seq += 1
        return self._swap_seq

    def next_notification_id(self) -> int:
        self._notification_seq += 1
        return self._notification_seq

    def user(self, user_id: str) -> Participant | Staff | None:
        return self.participants.get(user_id) or self.staff.get(user_id)

    def active_swap_of(self, participant_id: str) -> Swap | None:
        return next(
            (s for s in self.swaps.values() if s.status in ACTIVE_SWAP_STATUSES and s.member(participant_id)),
            None,
        )

    def staff_of_camps(self, camp_ids: set[int], roles: set[str]) -> list[Staff]:
        return [s for s in self.staff.values() if s.role in roles and camp_ids & set(s.camp_ids)]


store = Store()
