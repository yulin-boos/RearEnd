from io import BytesIO
import json
import os
import time

from fastapi.testclient import TestClient
from PIL import Image
import pytest

from Common.config import ROOT, Settings
from Visual.recognition.labels import LabelCatalog
from API.main import create_app
from Visual.recognition.postprocess import BACKEND, rank_probabilities
from Common.schemas import LeafCheck, Prediction


class StubRecognizer:
    """Use deterministic model outputs to verify HTTP behavior independently of accuracy."""

    def __init__(self, settings):
        self.task = "classify"
        self.postprocessor = BACKEND
        catalog = LabelCatalog()
        self.classes = [catalog.describe(0, "Apple___Apple_scab"), catalog.describe(1, "Apple___healthy")]

    def load(self):
        pass

    def check_leaf(self, image):
        return LeafCheck(is_leaf=True, model="stub", leaf_similarity=0.8,
                         competing_similarity=0.2, competing_category="person", margin=0.6,
                         minimum_similarity=0.24, minimum_margin=0.02, elapsed_ms=1.0)

    def predict(self, image, top_k):
        return [Prediction(**self.classes[index].model_dump(), confidence=score)
                for index, score in rank_probabilities([0.8, 0.2], top_k)], 1.0


def image_bytes(mode="RGB", size=(64, 48), image_format="PNG"):
    buffer = BytesIO()
    Image.new(mode, size, color="green").save(buffer, format=image_format)
    return buffer.getvalue()


@pytest.fixture
def settings(tmp_path):
    return Settings(result_dir=tmp_path / "results", chat_db_path=tmp_path / "chat.sqlite3",
                    knowledge_db_path=tmp_path / "knowledge.sqlite3", max_upload_mb=1, max_image_pixels=10_000,
                    cors_origins=["http://localhost:5173"])


@pytest.fixture
def client(settings):
    with TestClient(create_app(settings, StubRecognizer)) as http:
        yield http


def test_recognition_and_download(client):
    response = client.post("/api/v1/recognize", files={"file": ("leaf.png", image_bytes(), "image/png")}, data={"top_k": 2})
    assert response.status_code == 200
    data = response.json()
    assert data["image"] == {"filename": "leaf.png", "width": 64, "height": 48}
    assert data["status"] == "recognized"
    assert data["top_prediction"]["display_name"] == "苹果 · 黑星病"
    assert data["top_prediction"]["is_healthy"] is False
    assert len(data["predictions"]) == 2
    assert data["postprocessor"] == BACKEND
    assert data["leaf_check"]["is_leaf"] is True
    result = client.get(data["result_image_url"])
    assert result.status_code == 200
    assert result.headers["content-type"] == "image/jpeg"
    with Image.open(BytesIO(result.content)) as image:
        assert image.height == 148


def test_uncertain_result_retains_candidate(client, settings):
    response = client.post("/api/v1/recognize", files={"file": ("leaf.png", image_bytes())},
                           data={"confidence": 0.9, "save_result": "false", "top_k": 50})
    data = response.json()
    assert response.status_code == 200
    assert data["status"] == "uncertain" and data["is_confident"] is False
    assert data["top_prediction"]["confidence"] == 0.8
    assert len(data["predictions"]) == 2
    assert data["result_image_url"] is None
    assert list(settings.result_dir.glob("*.jpg")) == []


@pytest.mark.parametrize("confidence", [0, 0.5, 1])
def test_nonleaf_does_not_run_disease_inference_or_save_image(settings, confidence):
    class RejectingRecognizer(StubRecognizer):
        def check_leaf(self, image):
            return super().check_leaf(image).model_copy(update={
                "is_leaf": False, "leaf_similarity": 0.1,
                "competing_similarity": 0.8, "margin": -0.7,
            })

        def predict(self, image, top_k):
            raise AssertionError("Disease model must not run on rejected images")

    with TestClient(create_app(settings, RejectingRecognizer)) as client:
        response = client.post("/api/v1/recognize", files={"file": ("other.png", image_bytes())},
                               data={"confidence": confidence, "save_result": "true"})
        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "not_leaf"
        assert error["leaf_check"]["is_leaf"] is False
        assert "top_prediction" not in response.json()
        assert list(settings.result_dir.glob("*.jpg")) == []


@pytest.mark.parametrize("payload,status", [(b"", 400), (b"not an image", 400), (image_bytes(image_format="GIF"), 415), (image_bytes(size=(200, 200)), 413)], ids=["empty", "invalid", "gif", "pixel_limit"])
def test_invalid_images(client, payload, status):
    response = client.post("/api/v1/recognize", files={"file": ("leaf.png", payload, "image/png")})
    assert response.status_code == status
    assert "error" in response.json()


@pytest.mark.parametrize("payload", [b"x" * (1024 * 1024 + 1), b"x" * (2 * 1024 * 1024)], ids=["file_limit", "body_limit"])
def test_upload_limits(client, payload):
    response = client.post("/api/v1/recognize", files={"file": ("large.png", payload)})
    assert response.status_code == 413


def test_chunked_upload_limit(client):
    # No Content-Length: exercise the streaming limit before multipart decoding.
    boundary = "test-boundary"
    def body():
        yield f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="large.png"\r\nContent-Type: image/png\r\n\r\n'.encode()
        for _ in range(20):
            yield b"x" * 65536
        yield f"\r\n--{boundary}--\r\n".encode()
    response = client.post("/api/v1/recognize", content=body(), headers={"Content-Type": f"multipart/form-data; boundary={boundary}"})
    assert response.status_code == 413


@pytest.mark.parametrize("parameters", [{"confidence": 1.1}, {"top_k": 0}, {"top_k": 51}, {"save_result": "invalid"}])
def test_parameter_validation(client, parameters):
    response = client.post("/api/v1/recognize", files={"file": ("leaf.png", image_bytes())}, data=parameters)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_classes_health_and_cors(client):
    assert client.get("/health").json()["class_count"] == 2
    assert client.get("/api/v1/classes").json()[1]["is_healthy"] is True
    response = client.options("/api/v1/recognize", headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "POST"})
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert client.get("/openapi.json").status_code == 200


def test_filename_is_not_used_as_storage_path(client, settings):
    response = client.post("/api/v1/recognize", files={"file": ("../../leaf.png", image_bytes())})
    data = response.json()
    assert data["image"]["filename"] == "leaf.png"
    assert (settings.result_dir / f"{data['request_id']}.jpg").is_file()
    assert client.get("/api/v1/results/not-a-valid-id.jpg").status_code == 422
    assert client.get(f"/api/v1/results/{'0' * 32}.jpg").status_code == 404


def test_expired_results(client, settings):
    response = client.post("/api/v1/recognize", files={"file": ("leaf.png", image_bytes())})
    data = response.json()
    path = settings.result_dir / f"{data['request_id']}.jpg"
    old = time.time() - settings.result_ttl_seconds - 1
    os.utime(path, (old, old))
    assert client.get(data["result_image_url"]).status_code == 404
    client.post("/api/v1/recognize", files={"file": ("leaf.png", image_bytes())})
    assert not path.exists()


@pytest.mark.integration
@pytest.mark.skipif(os.getenv("YOLO_TEST_REAL_MODEL") != "1", reason="Set YOLO_TEST_REAL_MODEL=1 for real inference")
def test_real_model_end_to_end(tmp_path):
    # Public samples verify acceptance/rejection; they are not an accuracy benchmark.
    settings = Settings(model_path=ROOT.parent / "model" / "best.pt", result_dir=tmp_path / "real-results",
                        chat_db_path=tmp_path / "real-chat.sqlite3", knowledge_db_path=tmp_path / "real-knowledge.sqlite3", cors_origins=["null"])
    with TestClient(create_app(settings)) as client:
        health = client.get("/health").json()
        assert health["task"] == "classify" and health["class_count"] == 38
        assert health["leaf_check"]["enabled"] is True
        assert all(item["crop"] is not None for item in client.get("/api/v1/classes").json())
        fixtures = ROOT.parent / "tests" / "fixtures" / "leaf_gate"
        sample = (fixtures / "leaf_Apple___healthy_0.jpg").read_bytes()
        response = client.post("/api/v1/recognize", headers={"Origin": "null"}, files={"file": ("leaf.jpg", sample)}, data={"top_k": 5})
        assert response.status_code == 200, response.text
        assert response.headers["access-control-allow-origin"] == "null"
        data = response.json()
        assert len(data["predictions"]) == 5
        assert all(0 <= prediction["confidence"] <= 1 for prediction in data["predictions"])
        assert data["top_prediction"] == data["predictions"][0]
        assert client.get(data["result_image_url"]).status_code == 200
        assert data["leaf_check"]["is_leaf"] is True
        saved_before = set(settings.result_dir.glob("*.jpg"))
        rows = json.loads((fixtures / "sources.json").read_text(encoding="utf-8"))
        for row in rows:
            response = client.post("/api/v1/recognize", files={"file": (row["name"], (fixtures / row["name"]).read_bytes())},
                                   data={"confidence": 0, "save_result": "false"})
            assert response.status_code == (200 if row["expected_leaf"] else 422), f"{row['name']}: {response.text}"
            if not row["expected_leaf"]:
                assert response.json()["error"]["code"] == "not_leaf"
        for color in ("green", "white", "black", "yellow", "red"):
            buffer = BytesIO()
            Image.new("RGB", (224, 224), color).save(buffer, format="PNG")
            rejected = client.post("/api/v1/recognize", files={"file": ("blank.png", buffer.getvalue())},
                                   data={"confidence": 0, "save_result": "true"})
            assert rejected.status_code == 422, color
            assert rejected.json()["error"]["code"] == "not_leaf"
        assert set(settings.result_dir.glob("*.jpg")) == saved_before
        # Exercise decoding after Ultralytics has loaded and patched Pillow globally.
        invalid = client.post("/api/v1/recognize", headers={"Origin": "null"}, files={"file": ("broken.png", b"broken", "image/png")})
        assert invalid.status_code == 400
        assert invalid.headers["access-control-allow-origin"] == "null"
