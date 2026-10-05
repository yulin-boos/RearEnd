from fastapi.testclient import TestClient
import pytest

from Common.config import Settings
from Knowledge.schemas import KnowledgeDraft
from tests.test_chat import make_app
from tests.test_knowledge import draft_data


@pytest.fixture
def client(tmp_path):
    settings = Settings(result_dir=tmp_path / "results", chat_db_path=tmp_path / "chat.sqlite3",
                        knowledge_db_path=tmp_path / "knowledge.sqlite3")

    def no_provider_calls(request):
        raise AssertionError("Browsing knowledge must not call an external provider")

    with TestClient(make_app(settings, no_provider_calls, jev_handler=no_provider_calls)) as instance:
        yield instance


def seed(client, index, crop="苹果"):
    data = draft_data()
    data.update(crop=crop, title=f"{crop}知识 {index}", question=f"{crop}叶片斑点如何观察，情况 {index}？")
    identifier, _ = client.app.state.chat.knowledge.store.save(KnowledgeDraft(**data), "a" * 32, "test", "test")
    with client.app.state.chat.knowledge.store.connection() as connection:
        connection.execute("UPDATE knowledge_entries SET created_at = 100 WHERE id = ?", (identifier,))
    return identifier


def test_recent_list_is_paged_stable_and_preserves_complete_content_without_provider_calls(client):
    identifiers = [seed(client, index) for index in range(4)]
    first = client.get("/api/v1/knowledge", params={"limit": 2}).json()
    second = client.get("/api/v1/knowledge", params={"limit": 2, "offset": 2}).json()
    assert [entry["id"] for entry in first + second] == identifiers[::-1]
    assert first[0]["answer"] == draft_data()["answer"]
    assert first[0]["applicability"] == draft_data()["applicability"]
    assert first[0]["uncertainty"] == draft_data()["uncertainty"]
    assert first[0]["source_type"] == "ai_summary" and first[0]["expert_verified"] is False
    assert "source_recognition_id" not in first[0] and "jev_model" not in first[0]
    assert client.get("/api/v1/knowledge", params={"offset": 4}).json() == []


def test_recent_crop_filter_and_unknown_crop(client):
    apple = seed(client, 1)
    seed(client, 2, "番茄")
    assert [entry["id"] for entry in client.get("/api/v1/knowledge", params={"crop": "苹果"}).json()] == [apple]
    assert client.get("/api/v1/knowledge", params={"crop": "不存在"}).json() == []


def test_search_keeps_matching_and_supports_paging(client):
    identifiers = [seed(client, index) for index in range(3)]
    first = client.get("/api/v1/knowledge", params={"query": "苹果斑点", "limit": 2}).json()
    second = client.get("/api/v1/knowledge", params={"query": "苹果斑点", "limit": 2, "offset": 2}).json()
    assert len(first) == 2 and len(second) == 1
    assert {entry["id"] for entry in first + second} == set(identifiers)
    assert client.get("/api/v1/knowledge", params={"query": "香蕉"}).json() == []


@pytest.mark.parametrize("params", [{"limit": 0}, {"limit": 51}, {"offset": -1}, {"offset": "bad"}, {"query": "病"}])
def test_invalid_browse_parameters_are_rejected(client, params):
    assert client.get("/api/v1/knowledge", params=params).status_code == 422


def test_empty_library_and_detail_remain_compatible(client):
    assert client.get("/api/v1/knowledge").json() == []
    identifier = seed(client, 1)
    listed = client.get("/api/v1/knowledge").json()[0]
    assert client.get(f"/api/v1/knowledge/{identifier}").json() == listed
    assert client.get("/api/v1/knowledge/" + "f" * 32).status_code == 404
