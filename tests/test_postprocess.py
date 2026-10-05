import random

import pytest

from Visual.recognition import postprocess
from Visual.recognition.images import decode_image
from Visual.recognition.labels import LabelCatalog
from Visual.recognition.recognizer import YoloRecognizer
from Common.config import Settings
from io import BytesIO
from PIL import Image


def test_cpp_and_python_agree_on_ranking():
    native = pytest.importorskip("Visual.recognition._native")
    generator = random.Random(42)
    for size in (1, 38, 1000):
        values = [generator.random() for _ in range(size)]
        for top_k in (1, 5, size + 1):
            assert native.rank_probabilities(values, top_k) == postprocess.python_rank_probabilities(values, top_k)
    assert native.rank_probabilities([0.5, 0.5, 0], 2) == [(0, 0.5), (1, 0.5)]


@pytest.mark.parametrize("probabilities,top_k", [([], 1), ([0.2], 0), ([float("nan")], 1), ([float("inf")], 1), ([-0.1], 1), ([1.1], 1)])
def test_invalid_model_outputs(probabilities, top_k):
    with pytest.raises(ValueError):
        postprocess.rank_probabilities(probabilities, top_k)
    with pytest.raises(ValueError):
        postprocess.python_rank_probabilities(probabilities, top_k)


def test_python_fallback(monkeypatch):
    monkeypatch.setattr(postprocess, "_native_rank", None)
    assert postprocess.rank_probabilities([0.1, 0.9], 1) == [(1, 0.9)]


def test_exif_rotation_and_transparency():
    source = Image.new("RGB", (32, 16), "green")
    exif = source.getexif()
    exif[274] = 6
    buffer = BytesIO()
    source.save(buffer, format="JPEG", exif=exif)
    assert decode_image(buffer.getvalue(), 1000).size == (16, 32)
    buffer = BytesIO()
    Image.new("RGBA", (16, 16), (0, 0, 0, 0)).save(buffer, format="PNG")
    assert decode_image(buffer.getvalue(), 1000).getpixel((0, 0)) == (255, 255, 255)


def test_unknown_label_does_not_claim_health():
    label = LabelCatalog().describe(99, "custom_category")
    assert label.display_name == "custom_category"
    assert label.crop is None and label.is_healthy is None


def test_missing_model_fails_at_startup(tmp_path):
    with pytest.raises(FileNotFoundError):
        YoloRecognizer(Settings(model_path=tmp_path / "missing.pt")).load()
