"""Bounded inline image inputs for the provider; never fetch user-supplied URLs."""
import base64
import binascii
import io
import warnings

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChatImage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    data_url: str = Field(max_length=360000)

    @model_validator(mode="after")
    def valid_image(self):
        try:
            header, encoded = self.data_url.split(",", 1)
            formats = {"data:image/png;base64": "PNG", "data:image/jpeg;base64": "JPEG",
                       "data:image/webp;base64": "WEBP", "data:image/gif;base64": "GIF"}
            if header not in formats:
                raise ValueError("Unsupported image format")
            raw = base64.b64decode(encoded, validate=True)
            with warnings.catch_warnings():
                warnings.simplefilter("error", Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format != formats[header] or image.width * image.height > 16_000_000:
                        raise ValueError("Invalid image format or dimensions")
                    image.verify()
        except (ValueError, binascii.Error, OSError, SyntaxError, UnidentifiedImageError,
                Image.DecompressionBombError, Image.DecompressionBombWarning) as exc:
            raise ValueError("Invalid image. Use a valid PNG, JPEG, GIF or WebP image.") from exc
        return self


def provider_message(role, content, images=()):
    if not images or role != "user":
        return {"role": role, "content": content}
    blocks = [{"type": "text", "text": content}]
    for item in images:
        image = item.model_dump() if isinstance(item, ChatImage) else item
        blocks.extend([{"type": "text", "text": "Image filename: " + image["name"]},
                       {"type": "image_url", "image_url": {"url": image["data_url"], "detail": "original"}}])
    return {"role": role, "content": blocks}
