"""
Utilities for preparing images
before sending them to a vision model.
"""

import base64

from io import BytesIO

from PIL import (
    Image,
    ImageOps,
)


# -------------------------------------------------------------


MAX_SIZE = 1280

JPEG_QUALITY = 85


# -------------------------------------------------------------


def optimize_image(image_file):

    """
    Returns a PIL Image
    optimized for local vision models.
    """

    img = Image.open(image_file)

    # Fix phone orientation
    img = ImageOps.exif_transpose(img)

    # Convert transparency
    if img.mode in ("RGBA", "LA", "P"):

        background = Image.new(
            "RGB",
            img.size,
            (255, 255, 255),
        )

        if img.mode == "P":
            img = img.convert("RGBA")

        background.paste(
            img,
            mask=img.split()[-1],
        )

        img = background

    elif img.mode != "RGB":

        img = img.convert("RGB")

    # Resize while preserving aspect ratio
    img.thumbnail(
        (MAX_SIZE, MAX_SIZE),
        Image.Resampling.LANCZOS,
    )

    return img


# -------------------------------------------------------------


def image_to_base64(image_file):

    """
    Converts an uploaded image
    into compressed base64.
    """

    img = optimize_image(image_file)

    buffer = BytesIO()

    img.save(
        buffer,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
    )

    return base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")


# -------------------------------------------------------------


def estimate_image_tokens(image):

    """
    Very rough estimate of how many
    image tokens a vision model
    will consume.

    Used for deciding whether
    to shrink further.
    """

    w, h = image.size

    pixels = w * h

    return pixels // 750


# -------------------------------------------------------------


def prepare_for_llm(image_file):

    """
    Returns

    image_b64,
    estimated_tokens
    """

    img = optimize_image(image_file)

    tokens = estimate_image_tokens(img)

    buffer = BytesIO()

    img.save(
        buffer,
        format="JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
    )

    encoded = base64.b64encode(
        buffer.getvalue()
    ).decode("utf-8")

    return encoded, tokens
