"""
Image Reader MCP Server

A simple MCP server that reads local and remote images
and returns them in the native MCP image content format for LLM consumption.

Supported formats: PNG, JPEG, WebP, GIF, BMP, TIFF, SVG, ICO
"""

import base64
import io
import mimetypes
import os
import time
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from PIL import Image

mcp = FastMCP("ImageReader")

DEFAULT_AWAIT_FOR_SECONDS = 3.0

SUPPORTED_MIME_TYPES = {
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/gif",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
    "image/x-icon",
    "image/vnd.microsoft.icon",
}

EXTENSION_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


def normalize_await_for_seconds(await_for_seconds: float) -> float:
    """Validate and normalize the wait duration argument."""
    try:
        seconds = float(await_for_seconds)
    except (TypeError, ValueError) as exc:
        raise ValueError("await_for_seconds must be a non-negative number.") from exc

    if seconds < 0:
        raise ValueError("await_for_seconds must be a non-negative number.")

    return seconds


def wait_for_local_file(file_path: str, await_for_seconds: float) -> str:
    """Wait for a local file to appear and return its resolved path."""
    seconds = normalize_await_for_seconds(await_for_seconds)
    resolved = os.path.abspath(os.path.expanduser(file_path))

    if os.path.isfile(resolved):
        return resolved

    deadline = time.monotonic() + seconds
    poll_interval = 0.05

    while time.monotonic() < deadline:
        time.sleep(min(poll_interval, deadline - time.monotonic()))
        if os.path.isfile(resolved):
            return resolved

    if os.path.isfile(resolved):
        return resolved

    raise TimeoutError(
        f"File did not appear within {seconds:g} seconds: {resolved}"
    )


def retry_remote_operation(operation, path: str, await_for_seconds: float):
    """Retry a remote operation with increasing delays and a mandatory final attempt."""
    seconds = normalize_await_for_seconds(await_for_seconds)
    deadline = time.monotonic() + seconds
    sleep_for = 0.2
    last_error = None

    while True:
        try:
            return operation()
        except ValueError:
            raise
        except (httpx.HTTPStatusError, httpx.RequestError) as exc:
            last_error = exc

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break

        time.sleep(min(sleep_for, remaining))
        sleep_for = min(sleep_for * 1.7, 2.0)

    if last_error is not None:
        raise TimeoutError(
            f"Remote resource did not appear within {seconds:g} seconds: {path}. "
            f"Last error: {last_error}"
        ) from last_error

    raise TimeoutError(f"Remote resource did not appear within {seconds:g} seconds: {path}")


def guess_mime_type(path: str) -> str:
    """Guess MIME type from file extension, falling back to mimetypes module."""
    ext = os.path.splitext(path)[1].lower()
    if ext in EXTENSION_TO_MIME:
        return EXTENSION_TO_MIME[ext]
    mime, _ = mimetypes.guess_type(path)
    return mime or "application/octet-stream"


def is_url(path: str) -> bool:
    """Check if the given path is an HTTP/HTTPS URL."""
    try:
        parsed = urlparse(path)
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def validate_mime_type(mime_type: str) -> None:
    """Raise ValueError if MIME type is not a supported image format."""
    if mime_type not in SUPPORTED_MIME_TYPES:
        raise ValueError(
            f"Unsupported image format: {mime_type}. "
            f"Supported: {', '.join(sorted(SUPPORTED_MIME_TYPES))}"
        )


def get_image_dimensions(data: bytes) -> tuple[int, int] | None:
    """Get image width and height using Pillow. Returns (width, height) or None."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            return img.size
    except Exception:
        return None


def read_local_bytes(file_path: str) -> tuple[bytes, str]:
    """Read a local image file and return (raw_bytes, mime_type)."""
    resolved = os.path.abspath(os.path.expanduser(file_path))

    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: {resolved}")

    mime_type = guess_mime_type(resolved)
    validate_mime_type(mime_type)

    with open(resolved, "rb") as f:
        data = f.read()

    return data, mime_type


def read_remote_bytes(url: str, timeout: int = 30) -> tuple[bytes, str]:
    """Download an image from a URL and return (raw_bytes, mime_type)."""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        response = client.get(url, headers={"User-Agent": "ImageReader-MCP/1.0"})
        response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    mime_type = content_type.split(";")[0].strip().lower()

    if mime_type not in SUPPORTED_MIME_TYPES:
        mime_type = guess_mime_type(urlparse(url).path)

    validate_mime_type(mime_type)
    return response.content, mime_type


def build_description(path: str, mime_type: str, size_bytes: int,
                      dimensions: tuple[int, int] | None) -> str:
    """Build the text description line for the image."""
    dim_str = f" ({dimensions[0]}x{dimensions[1]})" if dimensions else ""
    return f"Image loaded{dim_str} — {mime_type}, {size_bytes} bytes, source: {path}"


def get_local_image_info_text(path: str) -> str:
    """Build metadata text for a local image path."""
    resolved = os.path.abspath(os.path.expanduser(path))
    if not os.path.isfile(resolved):
        raise FileNotFoundError(f"File not found: {resolved}")

    mime_type = guess_mime_type(resolved)
    size = os.path.getsize(resolved)

    dimensions = None
    try:
        with open(resolved, "rb") as f:
            dimensions = get_image_dimensions(f.read())
    except Exception:
        pass

    dim_str = f"\nresolution: {dimensions[0]}x{dimensions[1]}" if dimensions else ""
    return (
        f"source: local\n"
        f"path: {resolved}\n"
        f"mime_type: {mime_type}\n"
        f"size_bytes: {size}"
        f"{dim_str}"
    )


def get_remote_image_info_text(path: str) -> str:
    """Build metadata text for a remote image URL."""
    with httpx.Client(timeout=30, follow_redirects=True) as client:
        head_resp = client.head(path, headers={"User-Agent": "ImageReader-MCP/1.0"})
        head_resp.raise_for_status()

        content_type = head_resp.headers.get("content-type", "")
        mime_type = content_type.split(";")[0].strip().lower()
        if mime_type not in SUPPORTED_MIME_TYPES:
            mime_type = guess_mime_type(urlparse(path).path)

        content_length = head_resp.headers.get("content-length", "unknown")

        dimensions = None
        try:
            get_resp = client.get(path, headers={"User-Agent": "ImageReader-MCP/1.0"})
            get_resp.raise_for_status()
            dimensions = get_image_dimensions(get_resp.content)
        except Exception:
            pass

    dim_str = f"\nresolution: {dimensions[0]}x{dimensions[1]}" if dimensions else ""
    return (
        f"source: url\n"
        f"url: {path}\n"
        f"mime_type: {mime_type}\n"
        f"size_bytes: {content_length}"
        f"{dim_str}"
    )


@mcp.tool()
def read_image(
    path: str,
    await_for_seconds: float = DEFAULT_AWAIT_FOR_SECONDS,
) -> list[TextContent | ImageContent]:
    """Read an image from a local file path or URL and return it for LLM vision.

    Supports PNG, JPEG, WebP, GIF, BMP, TIFF, SVG, and ICO formats.
    For local files, provide the absolute or relative file path.
    For remote images, provide the full HTTP/HTTPS URL.
    Missing local or remote resources are awaited for up to the configured time.

    Returns the image in native MCP format (text description + image content).

    Args:
        path: Local file path or HTTP/HTTPS URL of the image to read.
        await_for_seconds: Seconds to wait for the image to appear before returning an error. Defaults to 3.0.
    """
    try:
        if is_url(path):
            data, mime_type = retry_remote_operation(
                lambda: read_remote_bytes(path), path, await_for_seconds
            )
        else:
            resolved = wait_for_local_file(path, await_for_seconds)
            data, mime_type = read_local_bytes(resolved)

        b64 = base64.b64encode(data).decode("ascii")
        dimensions = get_image_dimensions(data)
        description = build_description(path, mime_type, len(data), dimensions)

        return [
            TextContent(type="text", text=description),
            ImageContent(type="image", data=b64, mimeType=mime_type),
        ]
    except (FileNotFoundError, TimeoutError, ValueError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Error: HTTP {e.response.status_code} when fetching {path}")]
    except httpx.RequestError as e:
        return [TextContent(type="text", text=f"Error: Failed to fetch {path} — {e}")]


@mcp.tool()
def get_image_info(
    path: str,
    await_for_seconds: float = DEFAULT_AWAIT_FOR_SECONDS,
) -> list[TextContent]:
    """Get metadata about an image without returning the full image data.

    Returns the MIME type, file size, and resolution. Useful for checking
    format support and dimensions before reading the full image.

    Args:
        path: Local file path or HTTP/HTTPS URL of the image to inspect.
        await_for_seconds: Seconds to wait for the image to appear before returning an error. Defaults to 3.0.
    """
    try:
        if is_url(path):
            text = retry_remote_operation(
                lambda: get_remote_image_info_text(path), path, await_for_seconds
            )
        else:
            resolved = wait_for_local_file(path, await_for_seconds)
            text = get_local_image_info_text(resolved)

        return [TextContent(type="text", text=text)]
    except (FileNotFoundError, TimeoutError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Error: HTTP {e.response.status_code} when fetching {path}")]
    except httpx.RequestError as e:
        return [TextContent(type="text", text=f"Error: Failed to fetch {path} — {e}")]


if __name__ == "__main__":
    mcp.run()
