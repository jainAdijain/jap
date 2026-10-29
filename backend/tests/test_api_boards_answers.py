import httpx

ANTHROPIC = {"ats": "greenhouse", "board_token": "Anthropic", "display_name": "Anthropic"}


async def test_companies_to_watch(api: httpx.AsyncClient) -> None:
    added = await api.post(
        "/api/boards",
        json={"ats": "greenhouse", "board_token": "Anthropic", "display_name": "Anthropic"},
    )
    assert added.status_code == 201 and added.json()["board_token"] == "anthropic"
    dup = await api.post(
        "/api/boards", json={"ats": "greenhouse", "board_token": "anthropic", "display_name": "A"}
    )
    assert dup.status_code == 409
    bad = await api.post(
        "/api/boards", json={"ats": "workday", "board_token": "x", "display_name": "X"}
    )
    assert bad.status_code == 422
    board_id = added.json()["id"]
    assert (await api.delete(f"/api/boards/{board_id}")).status_code == 204
    assert (await api.get("/api/boards")).json() == []
    again = await api.post(
        "/api/boards",
        json={"ats": "greenhouse", "board_token": "anthropic", "display_name": "Anthropic"},
    )
    assert again.status_code == 201 and again.json()["id"] == board_id


async def test_answer_bank(api: httpx.AsyncClient) -> None:
    a = await api.post("/api/answers", json={"question": "Notice period?", "answer": "30 days"})
    assert a.status_code == 201
    dup = await api.post("/api/answers", json={"question": "notice  period", "answer": "x"})
    assert dup.status_code == 409
    edited = await api.put(
        f"/api/answers/{a.json()['id']}", json={"question": "Notice period?", "answer": "15 days"}
    )
    assert edited.json()["answer"] == "15 days"
    assert [x["answer"] for x in (await api.get("/api/answers")).json()] == ["15 days"]
    assert (await api.delete(f"/api/answers/{a.json()['id']}")).status_code == 204
    assert (await api.get("/api/answers")).json() == []
    missing = await api.delete(f"/api/answers/{a.json()['id']}")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"
