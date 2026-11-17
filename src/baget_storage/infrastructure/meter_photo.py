import asyncio
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

from baget_storage.core.exceptions import BusinessRuleViolation
from baget_storage.schemas.meter import ReadingPhotoDTO


class PillowMeterPhotoProcessor:
    async def process(self, content: bytes) -> ReadingPhotoDTO:
        if not content or len(content) > 8 * 1024 * 1024:
            raise BusinessRuleViolation("A photo must contain between 1 byte and 8 MiB.")
        return await asyncio.to_thread(self._process, content)

    @staticmethod
    def _process(content: bytes) -> ReadingPhotoDTO:
        try:
            with Image.open(BytesIO(content), formats=["JPEG", "PNG"]) as source:
                if source.width * source.height > 16_000_000:
                    raise BusinessRuleViolation("A photo must not exceed 16 megapixels.")
                source.load()
                output = BytesIO()
                with ImageOps.exif_transpose(source).convert("RGB") as normalized:
                    normalized.thumbnail((3000, 3000))
                    normalized.save(output, format="JPEG", quality=90)
                normalized_content = output.getvalue()
                if len(normalized_content) > 8 * 1024 * 1024:
                    raise BusinessRuleViolation("The normalized photo exceeds 8 MiB.")
                return ReadingPhotoDTO(content=normalized_content)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
            raise BusinessRuleViolation("A valid JPEG or PNG photo is required.") from exc
