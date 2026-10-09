import asyncio
import json
import sqlite3
import time

from fastapi.testclient import TestClient
import httpx
import pytest

from Chat.guidance_schemas import GuidanceRequest
from Common.errors import ChatError
from tests.test_chat import CandidateRecognizer, completion, jev_result, make_app, recognize, settings
from tests.test_users import bearer, register


def question(field, text=None):
    return {"id": field, "question": text or {"crop": "实际是什么作物？", "symptoms": "叶片有什么斑点？",
            "onset": "症状出现多久了？", "spread": "是否在扩散？", "environment": "种植环境怎样？"}[field],
            "reason": "用于区分候选病因和下一步观察。", "options": []}


def draft(questions=None, observations=None):
    return {"analysis": "目前只是候选结果，需要结合实际作物和症状核实。",
            "advice": ["观察叶片正反面，并记录症状变化。"],
            "questions": questions if questions is not None else [question("symptoms"), question("crop"), question("onset")],
            "observations": observations or []}


def guided_completion(data=None, **kwargs):
    return completion(json.dumps(data if data is not None else draft(), ensure_ascii=False), **kwargs)


def identify(client, **params):
    return recognize(client, auto_analyze="false", confidence=0.9, **params).json()["request_id"]


def generate(client, identifier, **fields):
    return client.post("/api/v1/chat/guidance", json={"recognition_id": identifier, **fields})


def saved(client, identifier):
    return client.get("/api/v1/chat/guidance/" + identifier)


def test_initial_guidance_uses_all_candidates_and_caches_a_complete_turn(settings):
    captured, checks = [], []
    def handler(request):
        payload = json.loads(request.content)
        captured.append(payload)
        return httpx.Response(200, json=guided_completion())
    def guard(request):
        checks.append(json.loads(request.content))
        return httpx.Response(200, json=jev_result())
    with TestClient(make_app(settings, handler, jev_handler=guard)) as client:
        identifier = identify(client)
        response = generate(client, identifier)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["revision"] == 1 and data["history_length"] == 2 and not data["stale"]
        assert data["questions"][0]["id"] == "crop" and len(data["questions"]) == 3
        assert data["confidence"]["top_confidence"] == 0.5
        assert data["confidence"]["threshold"] == 0.9 and "low_confidence" in data["confidence"]["uncertainty_reasons"]
        assert data["answered_information"] == {}
        assert captured[0]["response_format"] == {"type": "json_object"}
        source = json.loads(captured[0]["messages"][0]["content"].split("本轮引导数据 JSON：\n")[1])
        assert len(source["recognition"]["candidates"]) == 5
        assert checks[-1]["state"]["assistant_reply"].find(data["questions"][0]["reason"]) >= 0
        assert saved(client, identifier).json() == data
        assert generate(client, identifier).json() == data
        assert len(captured) == 1
        history = client.app.state.chat.store.get(identifier)["history"]
        assert [item["role"] for item in history] == ["user", "assistant"]
        assert data["analysis"] in history[-1]["content"]


def test_answers_change_questions_and_survive_restart_and_history_trimming(settings):
    settings = settings.model_copy(update={"chat_history_turns": 1})
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if len(calls) == 1:
            result = draft()
        elif len(calls) == 2:
            result = draft([question("crop"), question("symptoms"), question("onset")])
            result["analysis"] = "用户确认是苹果并报告褐色斑点，仍需结合出现时间核实。"
        else:
            result = draft([])
            result["advice"] = ["结合三天内的变化继续记录斑点扩散。"]
        return httpx.Response(200, json=guided_completion(result))
    with TestClient(make_app(settings, handler)) as client:
        identifier = identify(client)
        first = generate(client, identifier).json()
        second = generate(client, identifier, revision=first["revision"], answers={"crop": "苹果", "symptoms": "褐色斑点"})
        assert second.status_code == 200, second.text
        second = second.json()
        assert [item["id"] for item in second["questions"]] == ["onset"]
        assert second["answered_information"] == {"crop": "苹果", "symptoms": "褐色斑点"}
        assert second["confidence"] == first["confidence"]
    with TestClient(make_app(settings, handler)) as client:
        assert saved(client, identifier).json() == second
        final = generate(client, identifier, revision=2, answers={"onset": "三天"})
        assert final.status_code == 200, final.text
        data = final.json()
        assert data["revision"] == 3 and data["status"] == "advice_ready" and data["questions"] == []
        assert data["answered_information"] == {"crop": "苹果", "symptoms": "褐色斑点", "onset": "三天"}
        assert data["history_length"] == 2
        source = json.loads(calls[-1]["messages"][0]["content"].split("本轮引导数据 JSON：\n")[1])
        assert source["answered_information"]["crop"] == "苹果"


def test_existing_chat_reports_and_later_corrections_refresh_guidance(settings):
    guided_inputs, normal_inputs = [], []
    def handler(request):
        payload = json.loads(request.content)
        if "response_format" not in payload:
            normal_inputs.append(json.loads(payload["messages"][0]["content"].split("本次识别结果 JSON：\n")[1]))
            return httpx.Response(200, json=completion())
        source = json.loads(payload["messages"][0]["content"].split("本轮引导数据 JSON：\n")[1])
        guided_inputs.append(source)
        quote = "实际是番茄，叶片发黄" if len(guided_inputs) == 1 else "纠正一下，实际是黄瓜"
        observations = [{"field": "crop", "quote": quote}]
        if len(guided_inputs) == 1:
            observations.append({"field": "symptoms", "quote": "叶片发黄"})
        return httpx.Response(200, json=guided_completion(draft([question("onset")], observations)))
    with TestClient(make_app(settings, handler)) as client:
        identifier = identify(client)
        assert client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "实际是番茄，叶片发黄"}).status_code == 200
        first = generate(client, identifier).json()
        assert first["revision"] == 2 and [item["id"] for item in first["questions"]] == ["onset"]
        assert first["answered_information"]["symptoms"] == "叶片发黄"
        assert client.post("/api/v1/chat", json={"recognition_id": identifier, "message": "纠正一下，实际是黄瓜"}).status_code == 200
        assert normal_inputs[-1]["reported_information"]["crop"] == "实际是番茄，叶片发黄"
        stale = saved(client, identifier).json()
        assert stale["stale"] and stale["revision"] == 3 and stale["history_length"] == 6
        assert generate(client, identifier, revision=3, answers={"onset": "三天"}).json()["error"]["code"] == "guidance_outdated"
        updated = generate(client, identifier, revision=3).json()
        assert updated["answered_information"]["crop"] == "纠正一下，实际是黄瓜"
        assert updated["confidence"] == first["confidence"]
        assert guided_inputs[-1]["user_sources"] == ["纠正一下，实际是黄瓜"]


def test_general_session_and_mandatory_missing_information(settings):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion(draft([]))))) as client:
        identifier = client.post("/api/v1/chat/sessions").json()["session_id"]
        assert saved(client, identifier).json()["error"]["code"] == "guidance_not_found"
        response = generate(client, identifier)
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["confidence"]["top_confidence"] is None and data["confidence"]["crop_candidates"] == []
        assert data["confidence"]["uncertainty_reasons"] == ["no_image_result"]
        assert [item["id"] for item in data["questions"]] == ["crop", "symptoms"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_confident_scores_and_close_crop_candidates_change_question_order(settings, ambiguous):
    class Recognizer(CandidateRecognizer):
        def predict(self, image, top_k):
            candidates, elapsed = super().predict(image, top_k)
            if ambiguous:
                return [candidates[0].model_copy(update={"confidence": 0.51}),
                        candidates[4].model_copy(update={"confidence": 0.49})], elapsed
            return candidates, elapsed
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion()), recognizer=Recognizer)) as client:
        identifier = recognize(client, auto_analyze="false", confidence=0.5).json()["request_id"]
        data = generate(client, identifier).json()
        assert data["confidence"]["is_confident"] and "low_confidence" not in data["confidence"]["uncertainty_reasons"]
        assert data["questions"][0]["id"] == ("crop" if ambiguous else "symptoms")
        if ambiguous:
            assert data["confidence"]["uncertainty_reasons"] == ["close_candidates", "different_crops"]
            assert [item["confidence"] for item in data["confidence"]["crop_candidates"]] == [0.51, 0.49]


def test_empty_next_step_does_not_save_extracted_facts(settings):
    result = draft([], [{"field": "crop", "quote": "苹果"}, {"field": "symptoms", "quote": "褐色斑点"}])
    result["advice"] = []
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion(result)))) as client:
        identifier = identify(client)
        response = generate(client, identifier, message="苹果有褐色斑点")
        assert response.status_code == 502 and response.json()["error"]["code"] == "guidance_invalid_response"
        assert "guidance" not in client.app.state.chat.store.get(identifier)["context"]


def test_free_supplement_extracts_only_actual_reports_and_preserves_explicit_answers(settings):
    count = 0
    def handler(request):
        nonlocal count
        count += 1
        observations = [] if count == 1 else [{"field": "crop", "quote": "我朋友说是番茄"}, {"field": "watering", "quote": "每天浇水"}]
        return httpx.Response(200, json=guided_completion(draft([question("onset")], observations)))
    with TestClient(make_app(settings, handler)) as client:
        identifier = identify(client)
        first = generate(client, identifier).json()
        response = generate(client, identifier, revision=1, answers={"crop": "苹果", "symptoms": "不清楚"},
                            message="我朋友说是番茄，我实际种的是苹果，每天浇水")
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["answered_information"]["crop"] == "苹果"
        assert data["answered_information"]["watering"] == "每天浇水"
        assert all(item["id"] not in ("crop", "symptoms") for item in data["questions"])


@pytest.mark.parametrize("kind", ["bad_json", "duplicate_questions", "unsupported_observation", "extra_field", "too_many_questions", "truncated"])
def test_invalid_or_unfinished_generation_never_changes_state(settings, kind):
    invalid = draft()
    if kind == "duplicate_questions": invalid["questions"] = [question("crop"), question("crop")]
    if kind == "unsupported_observation": invalid["observations"] = [{"field": "crop", "quote": "我种的是水稻"}]
    if kind == "extra_field": invalid["diagnosed"] = True
    if kind == "too_many_questions": invalid["questions"].append(question("spread"))
    result = completion("broken") if kind == "bad_json" else guided_completion(invalid, finish_reason="length" if kind == "truncated" else "stop")
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=result))) as client:
        identifier = identify(client)
        failed = generate(client, identifier)
        assert failed.status_code == 502, failed.text
        assert client.app.state.chat.store.get(identifier)["revision"] == 0
        assert client.app.state.chat.store.get(identifier)["history"] == []
        assert saved(client, identifier).status_code == 404


@pytest.mark.parametrize("fields,status,code", [
    ({"answers": {"crop": "苹果"}}, 422, "validation_error"),
    ({"revision": 0, "answers": {"crop": "苹果"}}, 409, "conversation_changed"),
    ({"revision": 1, "answers": {"watering": "每天"}}, 422, "guidance_answer_unexpected"),
    ({"message": "每天浇水"}, 422, "guidance_revision_required"),
    ({"revision": 1, "answers": {"crop": " "}}, 422, "validation_error"),
    ({"revision": True}, 422, "validation_error"),
    ({"candidates": []}, 422, "validation_error"),
])
def test_bad_followups_are_rejected_before_generating(settings, fields, status, code):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(200, json=guided_completion())
    with TestClient(make_app(settings, handler)) as client:
        identifier = identify(client)
        first = generate(client, identifier).json()
        failed = generate(client, identifier, **fields)
        assert failed.status_code == status and failed.json()["error"]["code"] == code
        assert saved(client, identifier).json() == first and len(calls) == 1


@pytest.mark.parametrize("stage", ["question", "reply"])
def test_jev_rejection_does_not_commit_guidance(settings, stage):
    generated = []
    def handler(request):
        generated.append(request)
        return httpx.Response(200, json=guided_completion())
    def guard(request):
        output = "assistant_reply" in json.loads(request.content)["state"]
        reject = output if stage == "reply" else not output
        return httpx.Response(200, json=jev_result(off_topic=0.99 if reject else 0.01))
    with TestClient(make_app(settings, handler, jev_handler=guard)) as client:
        identifier = identify(client)
        failed = generate(client, identifier)
        assert failed.status_code == (502 if stage == "reply" else 403), failed.text
        assert len(generated) == int(stage == "reply")
        assert client.app.state.chat.store.get(identifier)["revision"] == 0


def test_ownership_and_expiry_apply_to_get_and_generate(settings):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion()))) as client:
        owner = register(client, "guide_owner").json()
        other = register(client, "guide_other").json()
        identifier = recognize(client, auto_analyze="false").json()["request_id"]
        # Assign a private owner before any guidance is generated.
        with sqlite3.connect(settings.chat_db_path) as connection:
            connection.execute("UPDATE conversations SET user_id=? WHERE recognition_id=?", (owner["user"]["id"], identifier))
        response = client.post("/api/v1/chat/guidance", headers=bearer(owner), json={"recognition_id": identifier})
        assert response.status_code == 200
        for headers in ({}, bearer(other)):
            assert client.get("/api/v1/chat/guidance/" + identifier, headers=headers).status_code == 404
            assert client.post("/api/v1/chat/guidance", headers=headers, json={"recognition_id": identifier}).status_code == 404
        with sqlite3.connect(settings.chat_db_path) as connection:
            connection.execute("UPDATE conversations SET expires_at=? WHERE recognition_id=?", (time.time() - 1, identifier))
        assert client.get("/api/v1/chat/guidance/" + identifier, headers=bearer(owner)).status_code == 404


def test_cross_process_revision_conflict_does_not_write_guidance(settings, monkeypatch):
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion()))) as client:
        identifier = identify(client)
        store = client.app.state.chat.store
        original = store.append_turn
        def racing(identifier, revision, question_text, reply, user_id=None, metadata=None):
            original(identifier, revision, "另一轮植物咨询", "需要继续观察。", user_id)
            return original(identifier, revision, question_text, reply, user_id, metadata)
        monkeypatch.setattr(store, "append_turn", racing)
        failed = generate(client, identifier)
        assert failed.status_code == 409 and failed.json()["error"]["code"] == "conversation_changed"
        session = store.get(identifier)
        assert session["revision"] == 1 and "guidance" not in session["context"]
        assert len(session["history"]) == 2


def test_guidance_and_chat_share_busy_lock(settings):
    async def run():
        ready, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            ready.set()
            await release.wait()
            return httpx.Response(200, json=guided_completion())
        app = make_app(settings, handler)
        async with app.router.lifespan_context(app):
            identifier = app.state.chat.create_general()
            task = asyncio.create_task(app.state.chat.guidance.generate(GuidanceRequest(recognition_id=identifier)))
            await asyncio.wait_for(ready.wait(), 5)
            try:
                with pytest.raises(ChatError) as blocked:
                    await app.state.chat.reply(identifier, "叶子发黄怎么办？")
                assert blocked.value.code == "chat_busy"
                with pytest.raises(ChatError) as blocked:
                    await app.state.chat.guidance.generate(GuidanceRequest(recognition_id=identifier))
                assert blocked.value.code == "chat_busy"
            finally:
                release.set()
                await task
            assert app.state.chat.store.get(identifier)["revision"] == 1
    asyncio.run(run())


@pytest.mark.parametrize("missing,error", [("deepseek_api_key", "deepseek_not_configured"), ("typesafe_api_key", "jev_not_configured")])
def test_missing_credentials_preserve_session(settings, missing, error):
    settings = settings.model_copy(update={missing: None})
    with TestClient(make_app(settings, lambda request: httpx.Response(200, json=guided_completion()))) as client:
        identifier = identify(client)
        response = generate(client, identifier)
        assert response.status_code == 503 and response.json()["error"]["code"] == error
        assert client.app.state.chat.store.get(identifier)["revision"] == 0
