import logging
import os
from threading import Lock
from time import perf_counter

from PIL import Image

from Common.config import ROOT, Settings
from Visual.recognition.labels import LabelCatalog
from Visual.recognition.leaf_gate import ClipLeafGate
from Visual.recognition.postprocess import BACKEND, rank_probabilities
from Common.schemas import ClassInfo, LeafCheck, Prediction

logger = logging.getLogger(__name__)


class YoloRecognizer:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.catalog = LabelCatalog()
        self.leaf_gate = ClipLeafGate(settings)
        self.model = None
        self.lock = Lock()
        self.classes: list[ClassInfo] = []
        self.postprocessor = BACKEND
        self.task = "classify"

    def load(self) -> None:
        if not self.settings.model_path.is_file():
            raise FileNotFoundError(f"找不到模型文件：{self.settings.model_path}")
        if self.settings.require_native and BACKEND != "cpp":
            raise RuntimeError("REQUIRE_NATIVE=true，但 C++ 模块尚未编译。请运行 build_native.ps1。")
        (ROOT / "storage" / "ultralytics").mkdir(parents=True, exist_ok=True)
        (ROOT / "storage" / "matplotlib").mkdir(parents=True, exist_ok=True)
        os.environ.setdefault("YOLO_CONFIG_DIR", str(ROOT / "storage" / "ultralytics"))
        os.environ.setdefault("MPLCONFIGDIR", str(ROOT / "storage" / "matplotlib"))
        os.environ.setdefault("YOLO_AUTOINSTALL", "false")
        self.leaf_gate.load()
        from ultralytics import YOLO

        self.model = YOLO(str(self.settings.model_path))
        if self.model.task != "classify":
            raise RuntimeError(f"当前接口需要 YOLO 分类模型，实际任务为 {self.model.task}。")
        self.classes = [self.catalog.describe(int(index), name) for index, name in sorted(self.model.names.items())]
        logger.info("Loaded %s: %s classes, postprocessor=%s", self.settings.model_path.name, len(self.classes), BACKEND)

    def check_leaf(self, image: Image.Image) -> LeafCheck:
        return self.leaf_gate.check(image)

    def predict(self, image: Image.Image, top_k: int) -> tuple[list[Prediction], float]:
        if self.model is None:
            raise RuntimeError("模型尚未加载")
        # Ultralytics maintains mutable predictor state; serialize access to one model.
        with self.lock:
            started = perf_counter()
            result = self.model.predict(
                source=image, imgsz=self.settings.image_size, device=self.settings.device,
                verbose=False, save=False,
            )[0]
            if result.probs is None:
                raise RuntimeError("模型没有返回分类概率")
            probabilities = result.probs.data.cpu().tolist()
            elapsed = (perf_counter() - started) * 1000
        if len(probabilities) != len(self.classes):
            raise RuntimeError("模型输出类别数量与类别映射不一致")
        predictions = [
            Prediction(**self.catalog.describe(index, self.model.names[index]).model_dump(), confidence=score)
            for index, score in rank_probabilities(probabilities, top_k)
        ]
        return predictions, round(elapsed, 3)
