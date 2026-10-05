from io import BytesIO
from pathlib import Path
import time

from fastapi import HTTPException
from PIL import Image, ImageDraw, ImageFont, ImageOps, UnidentifiedImageError
# Preserve Pillow's opener before Ultralytics patches Image.open with a HEIF fallback.
from PIL.Image import open as open_pillow_image

from Common.schemas import Prediction

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP", "BMP"}


def decode_image(data: bytes, max_pixels: int) -> Image.Image:
    if not data:
        raise HTTPException(400, "图片文件为空")
    try:
        with open_pillow_image(BytesIO(data)) as original:
            if original.format not in SUPPORTED_FORMATS:
                raise HTTPException(415, "支持 JPEG、PNG、WebP 和 BMP 图片")
            if original.width * original.height > max_pixels:
                raise HTTPException(413, "图片像素数超过限制，请缩小图片后重试")
            if getattr(original, "is_animated", False):
                raise HTTPException(415, "请上传静态图片")
            original.load()
            oriented = ImageOps.exif_transpose(original)
            if "A" in oriented.getbands() or "transparency" in oriented.info:
                rgba = oriented.convert("RGBA")
                background = Image.new("RGB", rgba.size, "white")
                background.paste(rgba, mask=rgba.getchannel("A"))
                return background
            return oriented.convert("RGB")
    except (Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
        raise HTTPException(413, "图片像素数超过限制") from exc
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError) as exc:
        raise HTTPException(400, "无法解码图片，文件可能已损坏或并非图片") from exc


def clean_filename(filename: str | None) -> str:
    return Path((filename or "image").replace("\\", "/")).name[:200] or "image"


def _font(size: int) -> tuple[ImageFont.ImageFont, bool]:
    for path in (
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
        Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
    ):
        if path.is_file():
            return ImageFont.truetype(str(path), size), True
    return ImageFont.load_default(size=size), False


def save_result_image(image: Image.Image, prediction: Prediction, confident: bool, path: Path) -> None:
    # Keep the image intact; classification does not provide lesion bounding boxes.
    preview = image.copy()
    preview.thumbnail((1600, 1600))
    font, chinese = _font(24)
    heading = prediction.display_name if chinese else prediction.class_name
    status = ("识别结果" if confident else "低置信度候选，请复核") if chinese else ("Prediction" if confident else "Low confidence: review required")
    lines = [f"{heading}  {prediction.confidence:.1%}", status]
    measure = ImageDraw.Draw(preview)
    text_width = max(measure.textbbox((0, 0), line, font=font)[2] for line in lines)
    canvas = Image.new("RGB", (max(preview.width, text_width + 32), preview.height + 100), "#152d25")
    canvas.paste(preview, ((canvas.width - preview.width) // 2, 100))
    draw = ImageDraw.Draw(canvas)
    for index, line in enumerate(lines):
        draw.text((16, 12 + index * 38), line, font=font, fill="white")
    # Write atomically so downloads never read a partially written result.
    temporary = path.with_suffix(".tmp")
    try:
        canvas.save(temporary, format="JPEG", quality=90)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def cleanup_results(directory: Path, ttl_seconds: int) -> None:
    cutoff = time.time() - ttl_seconds
    for path in directory.glob("*.jpg"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        except FileNotFoundError:
            pass
