import json

from Common.config import ROOT
from Common.schemas import ClassInfo


class LabelCatalog:
    def __init__(self) -> None:
        self.translations = json.loads((ROOT / "config" / "labels_zh.json").read_text(encoding="utf-8"))

    def describe(self, class_id: int, class_name: str) -> ClassInfo:
        entry = self.translations.get(class_name)
        if entry is None:
            return ClassInfo(class_id=class_id, class_name=class_name, display_name=class_name)
        return ClassInfo(
            class_id=class_id, class_name=class_name,
            display_name=f"{entry['crop']} · {entry['condition']}", **entry,
        )
