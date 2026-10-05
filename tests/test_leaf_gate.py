from Common.config import Settings
from Visual.recognition.leaf_gate import ClipLeafGate
import pytest


def test_missing_leaf_model_fails_closed(tmp_path):
    gate = ClipLeafGate(Settings(leaf_model_path=tmp_path / "missing"))
    with pytest.raises(FileNotFoundError, match="download_leaf_model"):
        gate.load()


@pytest.mark.parametrize("variable", ["LEAF_MIN_SIMILARITY", "LEAF_MIN_MARGIN"])
def test_invalid_leaf_thresholds_fail_configuration(monkeypatch, variable):
    monkeypatch.setenv(variable, "-0.1")
    with pytest.raises(ValueError):
        Settings.from_env()
