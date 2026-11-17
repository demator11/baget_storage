from datetime import date
from io import BytesIO

import pytest
from PIL import Image

from baget_storage.core.exceptions import BusinessRuleViolation
from baget_storage.domain.entities.reading_period import ReadingPeriodPolicy
from baget_storage.infrastructure.meter_photo import PillowMeterPhotoProcessor


@pytest.mark.parametrize(
    ("today", "is_open"),
    [
        (date(2026, 10, 31), False),
        (date(2026, 11, 1), True),
        (date(2026, 12, 5), True),
        (date(2026, 12, 6), False),
    ],
)
def test_submission_window_boundaries(today, is_open) -> None:
    result = ReadingPeriodPolicy().window(date(2026, 11, 1), today)
    assert result.is_open is is_open
    assert result.opens_on == date(2026, 11, 1)
    assert result.closes_on == date(2026, 12, 5)


def test_december_deadline_rolls_into_next_year() -> None:
    result = ReadingPeriodPolicy().window(date(2026, 12, 1), date(2027, 1, 5))
    assert result.closes_on == date(2027, 1, 5)
    assert result.is_open


def test_invalid_month_is_business_error() -> None:
    with pytest.raises(BusinessRuleViolation):
        ReadingPeriodPolicy().window(date(2026, 11, 2), date(2026, 11, 17))


async def test_photo_is_decoded_and_normalized_without_embedded_payload() -> None:
    original = BytesIO()
    Image.new("RGB", (20, 30), "white").save(original, format="PNG")
    result = await PillowMeterPhotoProcessor().process(
        original.getvalue() + b"untrusted trailing bytes"
    )
    assert result.content_type == "image/jpeg"
    assert b"untrusted trailing bytes" not in result.content
    with Image.open(BytesIO(result.content)) as decoded:
        assert decoded.format == "JPEG"
        assert decoded.size == (20, 30)


@pytest.mark.parametrize(
    "content", [b"", b"not a photo", b"\xff\xd8\xfftruncated", b"x" * (8 * 1024 * 1024 + 1)]
)
async def test_invalid_or_oversized_photo_is_rejected(content) -> None:
    with pytest.raises(BusinessRuleViolation):
        await PillowMeterPhotoProcessor().process(content)
