import math

try:
    from ._native import rank_probabilities as _native_rank
except ImportError:
    _native_rank = None

BACKEND = "cpp" if _native_rank is not None else "python"


def python_rank_probabilities(probabilities: list[float], top_k: int) -> list[tuple[int, float]]:
    if top_k < 1:
        raise ValueError("top_k must be positive")
    if not probabilities:
        raise ValueError("probabilities cannot be empty")
    if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
        raise ValueError("probabilities must be finite numbers in [0, 1]")
    return sorted(enumerate(probabilities), key=lambda item: (-item[1], item[0]))[:top_k]


def rank_probabilities(probabilities: list[float], top_k: int) -> list[tuple[int, float]]:
    if _native_rank is not None:
        return _native_rank(probabilities, top_k)
    return python_rank_probabilities(probabilities, top_k)
