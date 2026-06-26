from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import tempfile
from pathlib import Path


@dataclass(frozen=True)
class WindowsOCRText:
    text: str
    lines: tuple[str, ...]


class WindowsOCRUnavailable(RuntimeError):
    pass


def extract_text_from_image(image_path: Path) -> WindowsOCRText:
    image_path = image_path.resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"OCR image does not exist: {image_path}")
    return _run_async(_extract_text_from_image_async(image_path))


def extract_text_from_image_region(image_path: Path, box: tuple[int, int, int, int]) -> WindowsOCRText:
    """Run Windows OCR against one cropped image region.

    The box uses Pillow coordinates: left, top, right, bottom.
    """

    try:
        from PIL import Image
    except ImportError as exc:
        raise WindowsOCRUnavailable("Pillow is required for cropped OCR regions.") from exc

    image_path = image_path.resolve()
    if not image_path.exists():
        raise FileNotFoundError(f"OCR image does not exist: {image_path}")

    with Image.open(image_path) as image:
        width, height = image.size
        left, top, right, bottom = (int(round(value)) for value in box)
        left = max(0, min(width, left))
        right = max(0, min(width, right))
        top = max(0, min(height, top))
        bottom = max(0, min(height, bottom))

        if right <= left or bottom <= top:
            return WindowsOCRText(text="", lines=())

        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".png")
        temp_path = Path(temp_file.name)
        temp_file.close()

        try:
            image.crop((left, top, right, bottom)).save(temp_path)
            return extract_text_from_image(temp_path)
        finally:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError:
                pass


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


async def _extract_text_from_image_async(image_path: Path) -> WindowsOCRText:
    try:
        from winrt.windows.graphics.imaging import BitmapDecoder, BitmapPixelFormat, SoftwareBitmap
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage import FileAccessMode, StorageFile
    except ImportError as exc:
        raise WindowsOCRUnavailable(
            "Windows OCR packages are not installed. Install the winrt-Windows.* OCR dependencies."
        ) from exc

    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        raise WindowsOCRUnavailable("Windows OCR engine could not be created for the current user language.")

    storage_file = await StorageFile.get_file_from_path_async(str(image_path))
    stream = await storage_file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    if bitmap.bitmap_pixel_format != BitmapPixelFormat.BGRA8:
        bitmap = SoftwareBitmap.convert(bitmap, BitmapPixelFormat.BGRA8)

    result = await engine.recognize_async(bitmap)
    lines = tuple(line.text.strip() for line in result.lines if line.text.strip())
    return WindowsOCRText(text=(result.text or "").strip(), lines=lines)
