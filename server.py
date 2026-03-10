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
from urllib.parse import urlparse

import httpx
from mcp.server.fastmcp import FastMCP
from mcp.types import ImageContent, TextContent
from PIL import Image

mcp = FastMCP("ImageReader")

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


@mcp.tool()
def read_image(path: str) -> list[TextContent | ImageContent]:
    """Read an image from a local file path or URL and return it for LLM vision.

    Supports PNG, JPEG, WebP, GIF, BMP, TIFF, SVG, and ICO formats.
    For local files, provide the absolute or relative file path.
    For remote images, provide the full HTTP/HTTPS URL.

    Returns the image in native MCP format (text description + image content).

    Args:
        path: Local file path or HTTP/HTTPS URL of the image to read.
    """
    try:
        if is_url(path):
            data, mime_type = read_remote_bytes(path)
        else:
            data, mime_type = read_local_bytes(path)

        b64 = base64.b64encode(data).decode("ascii")
        dimensions = get_image_dimensions(data)
        description = build_description(path, mime_type, len(data), dimensions)

        return [
            TextContent(type="text", text=description),
            ImageContent(type="image", data=b64, mimeType=mime_type),
        ]
    except (FileNotFoundError, ValueError) as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Error: HTTP {e.response.status_code} when fetching {path}")]
    except httpx.RequestError as e:
        return [TextContent(type="text", text=f"Error: Failed to fetch {path} — {e}")]


@mcp.tool()
def get_image_info(path: str) -> list[TextContent]:
    """Get metadata about an image without returning the full image data.

    Returns the MIME type, file size, and resolution. Useful for checking
    format support and dimensions before reading the full image.

    Args:
        path: Local file path or HTTP/HTTPS URL of the image to inspect.
    """
    try:
        if is_url(path):
            # First try HEAD for size, then GET for resolution
            with httpx.Client(timeout=30, follow_redirects=True) as client:
                head_resp = client.head(
                    path, headers={"User-Agent": "ImageReader-MCP/1.0"}
                )
                head_resp.raise_for_status()

            content_type = head_resp.headers.get("content-type", "")
            mime_type = content_type.split(";")[0].strip().lower()
            if mime_type not in SUPPORTED_MIME_TYPES:
                mime_type = guess_mime_type(urlparse(path).path)

            content_length = head_resp.headers.get("content-length", "unknown")

            # Try to get dimensions by downloading the image
            dimensions = None
            try:
                with httpx.Client(timeout=30, follow_redirects=True) as client:
                    get_resp = client.get(
                        path, headers={"User-Agent": "ImageReader-MCP/1.0"}
                    )
                    get_resp.raise_for_status()
                dimensions = get_image_dimensions(get_resp.content)
            except Exception:
                pass

            dim_str = f"\nresolution: {dimensions[0]}x{dimensions[1]}" if dimensions else ""
            return [TextContent(type="text", text=(
                f"source: url\n"
                f"url: {path}\n"
                f"mime_type: {mime_type}\n"
                f"size_bytes: {content_length}"
                f"{dim_str}"
            ))]
        else:
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
            return [TextContent(type="text", text=(
                f"source: local\n"
                f"path: {resolved}\n"
                f"mime_type: {mime_type}\n"
                f"size_bytes: {size}"
                f"{dim_str}"
            ))]
    except FileNotFoundError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except ValueError as e:
        return [TextContent(type="text", text=f"Error: {e}")]
    except httpx.HTTPStatusError as e:
        return [TextContent(type="text", text=f"Error: HTTP {e.response.status_code} when fetching {path}")]
    except httpx.RequestError as e:
        return [TextContent(type="text", text=f"Error: Failed to fetch {path} — {e}")]


if __name__ == "__main__":
    mcp.run()
