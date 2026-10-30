import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.matching import build_graph, find_cycles, select_cycles


@pytest.fixture()
def client():
    with TestClient(app) as c:
        c.post("/api/v1/demo/reset")
        yield c


def h(user_id: str) -> dict:
    return {"X-User-Id": user_id}


def test_four_person_chain_end_to_end(client):
    r = client.put("/api/v1/me/request", json={"wishes": [8, 17]}, headers=h("p004"))
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "in_swap"
    swap_id = body["active_swap_id"]

    swap = client.get(f"/api/v1/swaps/{swap_id}", headers=h("p004")).json()
    moves = {m["participant_id"]: (m["from_camp"]["id"], m["to_camp"]["id"]) for m in swap["members"]}
    assert moves == {"p001": (8, 7), "p002": (7, 3), "p003": (3, 13), "p004": (13, 8)}
    assert all(m["contacts"] is None for m in swap["members"])

    # Решение до подтверждения всеми — конфликт
    r = client.post(f"/api/v1/swaps/{swap_id}/decision", json={"decision": "approve"}, headers=h("approver1"))
    assert r.status_code == 409

    for pid in ("p001", "p002", "p003", "p004"):
        r = client.patch(f"/api/v1/swaps/{swap_id}/members/me", json={"response": "accepted"}, headers=h(pid))
        assert r.status_code == 200, r.text
    swap = r.json()
    assert swap["status"] == "confirmed"
    assert all(m["contacts"] for m in swap["members"])

    queue = client.get("/api/v1/swaps", params={"status": "confirmed"}, headers=h("approver1")).json()
    assert swap_id in [s["id"] for s in queue]

    r = client.post(f"/api/v1/swaps/{swap_id}/decision", json={"decision": "approve"}, headers=h("approver1"))
    assert r.status_code == 200 and r.json()["status"] == "approved"

    assert client.get("/api/v1/me", headers=h("p004")).json()["camp"]["id"] == 8
    roster = client.get("/api/v1/camps/8/roster", headers=h("leader1")).json()
    gregory = next(p for p in roster["participants"] if p["participant_id"] == "p004")
    assert gregory["arrived_by_swap_id"] == swap_id and gregory["email"] and gregory["birth_date"] is None
    roster = client.get("/api/v1/camps/8/roster", headers=h("coord1")).json()
    assert all(p["birth_date"] and p["email"] is None for p in roster["participants"])

    notes = client.get("/api/v1/me/notifications", headers=h("leader1")).json()
    assert any(n["event"] == "swap.approved" for n in notes)
    csv_text = client.get("/api/v1/exports/approved-swaps.csv", headers=h("approver1")).text
    assert "Григорий Павлов" in csv_text


def test_seeded_swaps_for_demo(client):
    swaps = {s["id"]: s for s in client.get("/api/v1/swaps", headers=h("approver1")).json()}
    by_member = {m["participant_id"]: s for s in swaps.values() for m in s["members"]}
    assert by_member["p005"]["status"] == "proposed" and by_member["p005"]["id"] == 1
    assert by_member["p007"]["status"] == "confirmed" and by_member["p007"]["id"] == 2


def test_decline_returns_everyone_to_matching_and_bans_combination(client):
    swap_id = client.put("/api/v1/me/request", json={"wishes": [8]}, headers=h("p004")).json()["active_swap_id"]
    r = client.patch(f"/api/v1/swaps/{swap_id}/members/me", json={"response": "declined"}, headers=h("p002"))
    assert r.json()["status"] == "declined"
    me = client.get("/api/v1/me", headers=h("p001")).json()
    assert me["request"]["status"] == "active" and me["active_swap_id"] is None
    run = client.post("/api/v1/matching/runs", headers=h("curator1")).json()
    assert swap_id not in run["proposed_swap_ids"]
    assert client.get("/api/v1/me", headers=h("p004")).json()["active_swap_id"] is None


def test_reject_requires_comment(client):
    r = client.post("/api/v1/swaps/2/decision", json={"decision": "reject"}, headers=h("approver1"))
    assert r.status_code == 422
    r = client.post("/api/v1/swaps/2/decision", json={"decision": "reject", "comment": "Билеты уже куплены"}, headers=h("approver1"))
    assert r.json()["status"] == "rejected" and r.json()["decision_comment"] == "Билеты уже куплены"


def test_manual_swap_validation(client):
    bad = {"members": [{"participant_id": "p001", "to_camp_id": 7}, {"participant_id": "p002", "to_camp_id": 3}]}
    assert client.post("/api/v1/swaps", json=bad, headers=h("p001")).status_code == 422
    ok = {"members": [{"participant_id": "p001", "to_camp_id": 7}, {"participant_id": "p002", "to_camp_id": 8}]}
    r = client.post("/api/v1/swaps", json=ok, headers=h("p001"))
    assert r.status_code == 201
    swap = r.json()
    assert swap["source"] == "manual"
    assert {m["participant_id"]: m["response"] for m in swap["members"]} == {"p001": "accepted", "p002": "pending"}


def test_access_and_validation_errors(client):
    assert client.get("/api/v1/me").status_code == 422  # нет заголовка
    assert client.get("/api/v1/me", headers=h("nobody")).status_code == 401
    assert client.post("/api/v1/swaps/2/decision", json={"decision": "approve"}, headers=h("p001")).status_code == 403
    assert client.get("/api/v1/camps/8/roster", headers=h("p001")).status_code == 403
    assert client.get("/api/v1/camps/3/roster", headers=h("leader1")).status_code == 403
    assert client.put("/api/v1/me/request", json={"wishes": [13]}, headers=h("p004")).status_code == 422
    assert client.put("/api/v1/me/request", json={"wishes": [1, 2, 3, 4, 5, 6]}, headers=h("p004")).status_code == 422
    assert client.delete("/api/v1/me/request", headers=h("p004")).status_code == 404
    assert client.delete("/api/v1/me/request", headers=h("p001")).status_code == 204


def test_catalog(client):
    camps = client.get("/api/v1/camps").json()
    assert len(camps) == 63
    kizhi = next(c for c in camps if c["id"] == 8)
    assert kizhi["want_to_leave"] == 1 and kizhi["want_to_join"] == 0


def test_selection_prefers_more_participants():
    # A и B могут поменяться парой, но цепочка A → B → C даёт обмен троим
    wishes = {"A": ["b", "c"], "B": ["a", "c"], "C": ["a"]}
    camps = {"A": "a", "B": "b", "C": "c"}
    cycles = find_cycles(build_graph(wishes, camps))
    chosen = select_cycles(cycles)
    assert len(chosen) == 1 and {pid for pid, _ in chosen[0]} == {"A", "B", "C"}
