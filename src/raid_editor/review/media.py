"""Browser review codecs, deliberately independent of final delivery encoders."""

from typing import Literal

ReviewMediaFormat = Literal["webm", "mp4"]


def review_encoding_args(media_format: ReviewMediaFormat) -> list[str]:
    """Encode small review proxies using the selected browser-compatible format."""
    if media_format == "webm":
        codecs = [
            "-c:v",
            "libvpx-vp9",
            "-deadline",
            "realtime",
            "-cpu-used",
            "6",
            "-row-mt",
            "1",
            "-b:v",
            "0",
            "-crf",
            "35",
            "-c:a",
            "libopus",
            "-b:a",
            "128k",
        ]
    elif media_format == "mp4":
        codecs = [
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
        ]
    else:
        raise ValueError(f"Unsupported review media format: {media_format}")
    return [*codecs, "-pix_fmt", "yuv420p", "-metadata", "artist=Neil Mitchell"]


def review_media_type(media_format: str) -> str:
    return "video/webm" if media_format == "webm" else "video/mp4"
