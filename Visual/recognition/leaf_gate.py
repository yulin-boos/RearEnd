import json
import math
from threading import Lock
from time import perf_counter

from PIL import Image

from Common.config import ROOT, Settings
from Common.schemas import LeafCheck


class ClipLeafGate:
    """Require clear leaf semantics before calling the closed-set disease model."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.model = None
        self.processor = None
        self.text_features = None
        self.lock = Lock()
        prompts = json.loads((ROOT / "config" / "leaf_prompts.json").read_text(encoding="utf-8"))
        self.leaf_prompts = prompts["leaf"]
        self.other_categories = list(prompts["non_leaf"])
        self.other_prompts = list(prompts["non_leaf"].values())
        if not self.leaf_prompts or not self.other_prompts:
            raise ValueError("Leaf and non-leaf prompts cannot be empty")

    def load(self) -> None:
        directory = self.settings.leaf_model_path
        if not (directory / "config.json").is_file():
            raise FileNotFoundError(f"找不到叶片检查模型：{directory}。请运行 python tools/download_leaf_model.py。")
        import torch
        from transformers import CLIPModel, CLIPProcessor

        # Load only local files; a missing model must never silently disable the gate.
        self.processor = CLIPProcessor.from_pretrained(str(directory), local_files_only=True, use_fast=False)
        self.model = CLIPModel.from_pretrained(str(directory), local_files_only=True).to(self.settings.leaf_device).eval()
        tokens = self.processor(text=self.leaf_prompts + self.other_prompts, return_tensors="pt", padding=True)
        tokens = {name: value.to(self.settings.leaf_device) for name, value in tokens.items()}
        with torch.inference_mode():
            features = self.model.get_text_features(**tokens).float()
            self.text_features = features / features.norm(dim=-1, keepdim=True)

    def check(self, image: Image.Image) -> LeafCheck:
        if self.model is None or self.processor is None or self.text_features is None:
            raise RuntimeError("叶片检查模型尚未加载")
        import torch

        with self.lock, torch.inference_mode():
            started = perf_counter()
            pixels = self.processor(images=image, return_tensors="pt")["pixel_values"].to(self.settings.leaf_device)
            features = self.model.get_image_features(pixel_values=pixels).float()
            features = features / features.norm(dim=-1, keepdim=True)
            similarities = (features @ self.text_features.T)[0]
            leaf_similarity = float(similarities[:len(self.leaf_prompts)].max().item())
            other_scores = similarities[len(self.leaf_prompts):]
            index = int(other_scores.argmax().item())
            competing_similarity = float(other_scores[index].item())
            elapsed = (perf_counter() - started) * 1000
        if not math.isfinite(leaf_similarity) or not math.isfinite(competing_similarity):
            raise RuntimeError("叶片检查模型返回了无效分数")
        margin = leaf_similarity - competing_similarity
        # Cosine similarities are diagnostic scores, not calibrated probabilities.
        accepted = leaf_similarity >= self.settings.leaf_min_similarity and margin >= self.settings.leaf_min_margin
        return LeafCheck(
            is_leaf=accepted, model=self.settings.leaf_model_path.name,
            leaf_similarity=leaf_similarity, competing_similarity=competing_similarity,
            competing_category=self.other_categories[index], margin=margin,
            minimum_similarity=self.settings.leaf_min_similarity,
            minimum_margin=self.settings.leaf_min_margin, elapsed_ms=round(elapsed, 3),
        )
