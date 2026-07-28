"""Investment analytics powered by local Ollama."""

from malta_housing.analysis.evaluator import evaluate_listing
from malta_housing.analysis.ranker import run_rank

__all__ = ["evaluate_listing", "run_rank"]
