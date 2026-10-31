"""Подбор обменов: граф желаний, поиск циклов из 2–4 участников и выбор
набора циклов без общих участников.

Это постановка задачи об обмене донорскими почками (kidney exchange,
cycle formulation): вершины — участники с активной заявкой, ребро A → B
означает «A хочет в лагерь B», обмен — цикл ограниченной длины.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import coo_array

MAX_WISHES = 5
SOLVER_TIME_LIMIT_SEC = 10

# Цикл: кортеж пар (участник, ранг). Участник получает лагерь следующего
# по циклу, ранг — место этого лагеря в его списке желаний (1 — самый желанный).
Cycle = tuple[tuple[str, int], ...]


@dataclass(frozen=True)
class Edge:
    target: str
    rank: int


def build_graph(wishes: dict[str, list[int]], camp_of: dict[str, int]) -> dict[str, list[Edge]]:
    """Ребро A → B, если лагерь участника B есть в списке желаний участника A.

    В граф попадают только участники с активной заявкой: отдать свой лагерь
    готов лишь тот, кто сам хочет уехать.
    """
    by_camp: dict[int, list[str]] = {}
    for pid in wishes:
        by_camp.setdefault(camp_of[pid], []).append(pid)

    graph: dict[str, list[Edge]] = {}
    for pid, camps in wishes.items():
        graph[pid] = [
            Edge(other, rank)
            for rank, camp_id in enumerate(camps, start=1)
            for other in by_camp.get(camp_id, [])
            if other != pid
        ]
    return graph


def find_cycles(graph: dict[str, list[Edge]], max_len: int = 4) -> list[Cycle]:
    """Все простые циклы длиной от 2 до max_len.

    Цикл начинается с участника с наименьшим id и проходит только через
    участников с большими id, поэтому каждый цикл находится ровно один раз.
    """
    cycles: list[Cycle] = []

    def extend(start: str, path: list[str], ranks: list[int]) -> None:
        for edge in graph.get(path[-1], ()):
            if edge.target == start:
                if len(path) >= 2:
                    cycles.append(tuple(zip(path, ranks + [edge.rank])))
            elif edge.target > start and edge.target not in path and len(path) < max_len:
                extend(start, path + [edge.target], ranks + [edge.rank])

    for start in sorted(graph):
        extend(start, [start], [])
    return cycles


def cycle_weight(cycle: Cycle, big: int) -> int:
    """Вес цикла: число участников важнее суммы приоритетов.

    big больше любой возможной суммы приоритетов, поэтому сравнение весов
    лексикографическое: (сколько людей получают обмен, насколько высокие приоритеты).
    """
    return len(cycle) * big + sum(MAX_WISHES + 1 - rank for _, rank in cycle)


def select_cycles(cycles: list[Cycle]) -> tuple[list[Cycle], str]:
    """Набор циклов без общих участников с максимальным суммарным весом.

    Задача целочисленного линейного программирования:
    max Σ w_j·x_j при Σ x_j ≤ 1 по циклам j, содержащим участника i, x_j ∈ {0, 1}.
    На больших пулах заявок решатель может не доказать оптимум за отведённое
    время, поэтому результат сравнивается с жадным выбором и берётся лучший.

    Второй элемент ответа говорит, насколько результат надёжен:
    optimal — решатель доказал оптимум по числу участников (приоритеты — с малым допуском);
    best_found — лимит времени истёк, взят лучший из найденного решателем и жадного выбора;
    greedy — решатель ничего не нашёл, взят жадный выбор; none — циклов нет.
    """
    if not cycles:
        return [], "none"
    nodes = sorted({pid for cycle in cycles for pid, _ in cycle})
    big = MAX_WISHES * len(nodes) + 1
    greedy = _select_greedy(cycles, big)
    exact, proven = _select_exact(cycles, nodes, big)
    if exact is None:
        return greedy, "greedy"
    if proven:
        return exact, "optimal"
    total = lambda chosen: sum(cycle_weight(c, big) for c in chosen)  # noqa: E731
    return (exact if total(exact) >= total(greedy) else greedy), "best_found"


def _select_exact(cycles: list[Cycle], nodes: list[str], big: int) -> tuple[list[Cycle] | None, bool]:
    """Решение ЦЛП через HiGHS (scipy.optimize.milp) и признак, доказан ли оптимум."""
    index = {pid: i for i, pid in enumerate(nodes)}
    weights = np.array([cycle_weight(c, big) for c in cycles], dtype=float)

    rows, cols = [], []
    for j, cycle in enumerate(cycles):
        for pid, _ in cycle:
            rows.append(index[pid])
            cols.append(j)
    a = coo_array((np.ones(len(rows)), (rows, cols)), shape=(len(nodes), len(cycles))).tocsr()

    # Допустимый зазор до оптимума меньше половины big: решатель не может
    # потерять ни одного участника, только немного в сумме приоритетов.
    result = milp(
        c=-weights,
        constraints=LinearConstraint(a, ub=np.ones(len(nodes))),
        integrality=np.ones(len(cycles)),
        bounds=Bounds(0, 1),
        options={"mip_rel_gap": 0.5 / len(nodes), "time_limit": SOLVER_TIME_LIMIT_SEC},
    )
    if result.x is None:
        return None, False
    return [cycles[j] for j in range(len(cycles)) if result.x[j] > 0.5], result.status == 0


def _select_greedy(cycles: list[Cycle], big: int) -> list[Cycle]:
    """Жадный выбор: циклы по убыванию веса, пропуская пересекающиеся."""
    chosen, used = [], set()
    for cycle in sorted(cycles, key=lambda c: cycle_weight(c, big), reverse=True):
        members = {pid for pid, _ in cycle}
        if not members & used:
            chosen.append(cycle)
            used |= members
    return chosen
