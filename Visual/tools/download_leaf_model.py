"""Download a pinned public CLIP model for the local leaf check."""
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent))
from Common.config import Settings

REPOSITORY = "openai/clip-vit-base-patch32"
REVISION = "3d74acf9a28c67741b2f4f2ea7635f0aaf6f0268"
FILES = ["config.json", "preprocessor_config.json", "tokenizer_config.json", "tokenizer.json",
         "special_tokens_map.json", "vocab.json", "merges.txt", "pytorch_model.bin", "README.md"]

if __name__ == "__main__":
    settings = Settings.from_env()
    cli = Path(sys.executable).parent / ("hf.exe" if os.name == "nt" else "hf")
    if not cli.is_file():
        raise SystemExit("Install requirements.txt into this Python environment first.")
    environment = os.environ.copy()
    environment["HF_HOME"] = str(ROOT / "storage" / "huggingface")
    environment["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
    environment["HF_HUB_DISABLE_XET"] = "1"
    subprocess.run([str(cli), "download", REPOSITORY, *FILES, "--revision", REVISION,
                    "--local-dir", str(settings.leaf_model_path), "--max-workers", "2"],
                   env=environment, check=True)
