"""Бизнес-правила заявок и обменов.

Функции вызываются под store.lock (см. main.py). В полной системе события
отсюда уходили бы в RabbitMQ, а уведомления отправлял бы отдельный сервис;
в демо они складываются в store.notifications, его можно посмотреть через API.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

from . import matching
from .store import (
    APPROVED, APPROVER, CAMP_LEADER, CONFIRMED, COORDINATOR, CURATOR, DECLINED, EXPIRED,
    PROPOSED, REJECTED, REQUEST_ACTIVE, REQUEST_FULFILLED, REQUEST_IN_SWAP, REQUEST_WITHDRAWN,
    AuditEntry, ExchangeRequest, Notification, Participant, Staff, Swap, SwapMember, store,
)

OFFER_TTL = timedelta(hours=float(os.getenv("OFFER_TTL_HOURS", "48")))
MAX_CYCLE_LEN = int(os.getenv("MAX_CYCLE_LEN", "4"))
MATCHING_SERVICE = "matching-service"


class DomainError(Exception):
    def __init__(self, status: int, detail: str) -> None:
        super().__init__(detail)
        self.status = status
        self.detail = detail


def now() -> datetime:
    return datetime.now(timezone.utc)


def log(actor: str, action: str, swap_id: int | None, details: str) -> None:
    store.audit.append(AuditEntry(now(), actor, action, swap_id, details))


def notify(user_id: str, event: str, text: str) -> None:
    user = store.user(user_id)
    channel = "vk" if isinstance(user, Participant) and user.vk_id else "email"
    store.notifications.append(Notification(store.next_notification_id(), user_id, channel, event, text, now()))


def camp_title(camp_id: int) -> str:
    camp = store.camps[camp_id]
    return f"{camp.name} ({camp.start_date:%d.%m}–{camp.end_date:%d.%m})"


def describe_moves(swap: Swap) -> str:
    return "; ".join(
        f"{store.participants[m.participant_id].full_name}: {camp_title(m.from_camp_id)} → {camp_title(m.to_camp_id)}"
        for m in swap.members
    )


def exchange_open() -> bool:
    season = store.season
    return season is not None and season.exchange_opens <= now().date() <= season.exchange_closes


# ---------- Заявки ----------

def save_request(participant: Participant, wishes: list[int]) -> tuple[ExchangeRequest, bool]:
    if not exchange_open():
        raise DomainError(403, "Период обменов закрыт: заявки не принимаются")
    if len(wishes) != len(set(wishes)):
        raise DomainError(422, "Лагеря в списке желаний не должны повторяться")
    unknown = [c for c in wishes if c not in store.camps]
    if unknown:
        raise DomainError(422, f"Нет лагерей с id {unknown} в текущем сезоне")
    if participant.camp_id in wishes:
        raise DomainError(422, "Нельзя указать в желаниях свой текущий лагерь")
    swap = store.active_swap_of(participant.id)
    if swap:
        raise DomainError(409, f"Заявка входит в обмен №{swap.id}: сначала ответьте на него или дождитесь решения фонда")

    existing = store.requests.get(participant.id)
    created = existing is None or existing.status in (REQUEST_WITHDRAWN, REQUEST_FULFILLED)
    if created:
        store.requests[participant.id] = ExchangeRequest(participant.id, list(wishes), REQUEST_ACTIVE, now(), now())
    else:
        existing.wishes = list(wishes)
        existing.status = REQUEST_ACTIVE
        existing.updated_at = now()
    log(participant.id, "request.saved", None, f"желания: {wishes}")
    return store.requests[participant.id], created


def withdraw_request(participant: Participant) -> None:
    request = store.requests.get(participant.id)
    if request is None or request.status not in (REQUEST_ACTIVE, REQUEST_IN_SWAP):
        raise DomainError(404, "Активной заявки нет")
    if request.status == REQUEST_IN_SWAP:
        raise DomainError(409, "Заявка входит в предложенный обмен: сначала отклоните обмен")
    request.status = REQUEST_WITHDRAWN
    request.updated_at = now()
    log(participant.id, "request.withdrawn", None, "")


# ---------- Обмены ----------

def create_swap(members: list[SwapMember], source: str, created_by: str | None) -> Swap:
    swap = Swap(store.next_swap_id(), source, PROPOSED, members, now(), now() + OFFER_TTL, created_by)
    store.swaps[swap.id] = swap
    for m in members:
        request = store.requests.get(m.participant_id)
        if request and request.status == REQUEST_ACTIVE:
            request.status = REQUEST_IN_SWAP
    log(created_by or MATCHING_SERVICE, "swap.proposed", swap.id, describe_moves(swap))
    for m in members:
        if m.participant_id == created_by:
            continue
        rank = f" (ваш приоритет №{m.wish_rank})" if m.wish_rank else ""
        notify(
            m.participant_id, "swap.proposed",
            f"Предложен обмен №{swap.id}: вы переходите в «{camp_title(m.to_camp_id)}»{rank}. "
            f"Кто куда: {describe_moves(swap)}. Ответьте до {swap.expires_at:%d.%m %H:%M} UTC.",
        )
    return swap


def propose_manual(initiator: Participant, moves: list[tuple[str, int]]) -> Swap:
    """Оформление обмена, о котором участники договорились сами (US05)."""
    if not exchange_open():
        raise DomainError(403, "Период обменов закрыт")
    ids = [pid for pid, _ in moves]
    if initiator.id not in ids:
        raise DomainError(422, "Инициатор должен быть среди участников обмена")
    if len(set(ids)) != len(ids):
        raise DomainError(422, "Каждый участник указывается один раз")
    unknown = [pid for pid in ids if pid not in store.participants]
    if unknown:
        raise DomainError(422, f"Неизвестные участники: {unknown}")
    if any(camp_id not in store.camps for _, camp_id in moves):
        raise DomainError(422, "Указан несуществующий лагерь")
    from_camps = sorted(store.participants[pid].camp_id for pid in ids)
    to_camps = sorted(camp_id for _, camp_id in moves)
    if from_camps != to_camps:
        raise DomainError(422, "Обмен не замкнут: каждый лагерь должен отдаваться и получаться ровно один раз")
    if any(store.participants[pid].camp_id == camp_id for pid, camp_id in moves):
        raise DomainError(422, "Участник не может перейти в свой же лагерь")
    busy = [pid for pid in ids if store.active_swap_of(pid)]
    if busy:
        raise DomainError(409, f"Участники {busy} уже входят в другой активный обмен")

    members = []
    for pid, camp_id in moves:
        request = store.requests.get(pid)
        rank = request.wishes.index(camp_id) + 1 if request and camp_id in request.wishes else None
        members.append(SwapMember(pid, store.participants[pid].camp_id, camp_id, rank))
    swap = create_swap(members, "manual", initiator.id)
    member = swap.member(initiator.id)
    member.response, member.responded_at = "accepted", now()
    return swap


def get_swap(swap_id: int) -> Swap:
    swap = store.swaps.get(swap_id)
    if swap is None:
        raise DomainError(404, f"Обмен №{swap_id} не найден")
    return swap


def close_swap(swap: Swap, status: str, actor: str, reason: str) -> None:
    """Неудачный исход: участники возвращаются в подбор, комбинация больше не предлагается."""
    swap.status = status
    store.banned.add(swap.combination())
    back_to_matching = set()
    for m in swap.members:
        request = store.requests.get(m.participant_id)
        if request and request.status == REQUEST_IN_SWAP:
            request.status = REQUEST_ACTIVE
            request.updated_at = now()
            back_to_matching.add(m.participant_id)
    log(actor, f"swap.{status}", swap.id, reason)
    # Участник договорённого обмена мог не подавать заявку: ему нечего возвращать в подбор.
    for m in swap.members:
        tail = ("Ваша заявка снова участвует в подборе." if m.participant_id in back_to_matching and exchange_open()
                else "Вы остаётесь в своём лагере.")
        notify(m.participant_id, f"swap.{status}", f"Обмен №{swap.id} не состоится: {reason}. {tail}")


def expire_reason(swap: Swap) -> str | None:
    """Почему предложение пора снять: истёк срок ответа или закончился период обменов."""
    if swap.status != PROPOSED:
        return None
    if swap.expires_at < now():
        return "не все участники ответили вовремя"
    if not exchange_open():
        return "период обменов закончился"
    return None


def respond(swap_id: int, participant: Participant, response: str) -> Swap:
    swap = get_swap(swap_id)
    member = swap.member(participant.id)
    if member is None:
        raise DomainError(403, "Вы не участник этого обмена")
    reason = expire_reason(swap)
    if reason:
        close_swap(swap, EXPIRED, MATCHING_SERVICE, reason)
    if swap.status != PROPOSED:
        raise DomainError(409, f"Обмен в статусе «{swap.status}», ответить на него уже нельзя")
    if member.response != "pending":
        raise DomainError(409, "Вы уже ответили на этот обмен")

    member.response, member.responded_at = response, now()
    if response == "declined":
        close_swap(swap, DECLINED, participant.id, f"{participant.full_name} отказался(ась) от обмена")
        return swap

    log(participant.id, "swap.accepted", swap.id, "")
    if all(m.response == "accepted" for m in swap.members):
        swap.status = CONFIRMED
        log(MATCHING_SERVICE, "swap.confirmed", swap.id, "все участники согласны")
        for m in swap.members:
            notify(m.participant_id, "swap.confirmed",
                   f"Все участники обмена №{swap.id} согласны. Контакты открыты, обмен передан на согласование в фонд.")
        for approver in (s for s in store.staff.values() if s.role == APPROVER):
            notify(approver.id, "swap.confirmed", f"Новый обмен №{swap.id} в очереди на согласование: {describe_moves(swap)}")
    return swap


def decide(swap_id: int, approver: Staff, decision: str, comment: str | None) -> Swap:
    swap = get_swap(swap_id)
    if swap.status != CONFIRMED:
        raise DomainError(409, f"Решение принимается только по подтверждённым обменам, а этот в статусе «{swap.status}»")
    if decision == "reject":
        if not comment or not comment.strip():
            raise DomainError(422, "Отклонить обмен без комментария нельзя")
        swap.decided_by, swap.decided_at, swap.decision_comment = approver.id, now(), comment.strip()
        close_swap(swap, REJECTED, approver.id, f"фонд отклонил обмен: {comment.strip()}")
        return swap

    # Применение одной «транзакцией»: сначала проверяем всех, потом меняем всех.
    moved = [m for m in swap.members if store.participants[m.participant_id].camp_id != m.from_camp_id]
    if moved:
        raise DomainError(409, "Распределение участников изменилось после подбора, обмен нельзя применить")
    for m in swap.members:
        participant = store.participants[m.participant_id]
        participant.camp_id = m.to_camp_id
        participant.arrived_by_swap_id = swap.id
        request = store.requests.get(m.participant_id)
        if request:
            request.status = REQUEST_FULFILLED
            request.updated_at = now()
    swap.status = APPROVED
    swap.decided_by, swap.decided_at, swap.decision_comment = approver.id, now(), (comment or "").strip() or None
    log(approver.id, "swap.approved", swap.id, describe_moves(swap))
    for m in swap.members:
        notify(m.participant_id, "swap.approved", f"Фонд согласовал обмен №{swap.id}. Ваш новый лагерь: «{camp_title(m.to_camp_id)}».")
    for staff in store.staff_of_camps(swap.camp_ids(), {CAMP_LEADER, COORDINATOR, CURATOR}):
        notify(staff.id, "swap.approved", f"Изменился состав лагерей по обмену №{swap.id}: {describe_moves(swap)}")
    return swap


# ---------- Обработчик по расписанию ----------

def expire_offers() -> list[int]:
    """Снимает предложения с истёкшим сроком ответа, а после окончания периода обменов — все
    неотвеченные. Подтверждённые участниками обмены остаются в очереди фонда."""
    expired = []
    for swap in list(store.swaps.values()):
        reason = expire_reason(swap)
        if reason:
            close_swap(swap, EXPIRED, MATCHING_SERVICE, reason)
            expired.append(swap.id)
    return expired


def run_matching(trigger: str) -> dict:
    """Один запуск сервиса подбора (алгоритм описан в ТЗ, раздел про обработчик по расписанию)."""
    started = now()
    t0 = time.perf_counter()
    report = {"trigger": trigger, "started_at": started, "expired_swap_ids": expire_offers(),
              "active_requests": 0, "graph_edges": 0, "cycles_found": 0, "proposed_swap_ids": [],
              "selection": None, "skipped_reason": None}

    if not exchange_open():
        report["skipped_reason"] = "период обменов закрыт"
    else:
        wishes = {pid: r.wishes for pid, r in store.requests.items() if r.status == REQUEST_ACTIVE}
        camp_of = {pid: store.participants[pid].camp_id for pid in wishes}
        graph = matching.build_graph(wishes, camp_of)
        cycles = []
        for cycle in matching.find_cycles(graph, MAX_CYCLE_LEN):
            members = [
                SwapMember(pid, camp_of[pid], camp_of[cycle[(i + 1) % len(cycle)][0]], rank)
                for i, (pid, rank) in enumerate(cycle)
            ]
            if frozenset((m.participant_id, m.to_camp_id) for m in members) not in store.banned:
                cycles.append((cycle, members))
        chosen, selection = matching.select_cycles([c for c, _ in cycles])
        chosen = set(chosen)
        report["proposed_swap_ids"] = [create_swap(members, "auto", None).id for cycle, members in cycles if cycle in chosen]
        report.update(active_requests=len(wishes), graph_edges=sum(len(e) for e in graph.values()),
                      cycles_found=len(cycles), selection=selection)

    report["duration_ms"] = round((time.perf_counter() - t0) * 1000, 1)
    return report
