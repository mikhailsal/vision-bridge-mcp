"""Unit tests for Image Reader MCP Server."""

import base64
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import httpx
from mcp.types import ImageContent, TextContent

from server import (
    SUPPORTED_MIME_TYPES,
    build_description,
    get_image_dimensions,
    get_image_info,
    guess_mime_type,
    is_url,
    read_image,
    read_local_bytes,
    read_remote_bytes,
    validate_mime_type,
)

# Minimal valid 1x1 red PNG (generated from raw spec)
MINIMAL_PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01"
    b"\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde\x00"
    b"\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00"
    b"\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
)


class TestIsUrl(unittest.TestCase):
    def test_http_url(self):
        self.assertTrue(is_url("http://example.com/image.png"))

    def test_https_url(self):
        self.assertTrue(is_url("https://example.com/photo.jpg"))

    def test_local_path(self):
        self.assertFalse(is_url("/home/user/image.png"))

    def test_relative_path(self):
        self.assertFalse(is_url("./images/photo.jpg"))

    def test_empty_string(self):
        self.assertFalse(is_url(""))

    def test_ftp_url(self):
        self.assertFalse(is_url("ftp://example.com/image.png"))


class TestGuessMimeType(unittest.TestCase):
    def test_png(self):
        self.assertEqual(guess_mime_type("photo.png"), "image/png")

    def test_jpg(self):
        self.assertEqual(guess_mime_type("photo.jpg"), "image/jpeg")

    def test_jpeg(self):
        self.assertEqual(guess_mime_type("photo.jpeg"), "image/jpeg")

    def test_webp(self):
        self.assertEqual(guess_mime_type("photo.webp"), "image/webp")

    def test_gif(self):
        self.assertEqual(guess_mime_type("anim.gif"), "image/gif")

    def test_bmp(self):
        self.assertEqual(guess_mime_type("image.bmp"), "image/bmp")

    def test_tiff(self):
        self.assertEqual(guess_mime_type("scan.tiff"), "image/tiff")

    def test_tif(self):
        self.assertEqual(guess_mime_type("scan.tif"), "image/tiff")

    def test_svg(self):
        self.assertEqual(guess_mime_type("icon.svg"), "image/svg+xml")

    def test_ico(self):
        self.assertEqual(guess_mime_type("favicon.ico"), "image/x-icon")

    def test_unknown_extension(self):
        result = guess_mime_type("file.xyz123")
        self.assertIsInstance(result, str)

    def test_case_insensitive(self):
        self.assertEqual(guess_mime_type("photo.PNG"), "image/png")

    def test_path_with_directories(self):
        self.assertEqual(guess_mime_type("/home/user/images/photo.png"), "image/png")


class TestValidateMimeType(unittest.TestCase):
    def test_supported_types(self):
        for mime in SUPPORTED_MIME_TYPES:
            validate_mime_type(mime)  # Should not raise

    def test_unsupported_type(self):
        with self.assertRaises(ValueError) as ctx:
            validate_mime_type("application/pdf")
        self.assertIn("Unsupported image format", str(ctx.exception))

    def test_empty_string(self):
        with self.assertRaises(ValueError):
            validate_mime_type("")

    def test_octet_stream(self):
        with self.assertRaises(ValueError):
            validate_mime_type("application/octet-stream")


class TestGetImageDimensions(unittest.TestCase):
    def test_valid_png(self):
        dims = get_image_dimensions(MINIMAL_PNG)
        self.assertEqual(dims, (1, 1))

    def test_invalid_data(self):
        dims = get_image_dimensions(b"not an image")
        self.assertIsNone(dims)

    def test_empty_data(self):
        dims = get_image_dimensions(b"")
        self.assertIsNone(dims)


class TestBuildDescription(unittest.TestCase):
    def test_with_dimensions(self):
        result = build_description("/tmp/img.png", "image/png", 1024, (800, 600))
        self.assertIn("800x600", result)
        self.assertIn("image/png", result)
        self.assertIn("1024 bytes", result)
        self.assertIn("/tmp/img.png", result)

    def test_without_dimensions(self):
        result = build_description("/tmp/img.svg", "image/svg+xml", 512, None)
        self.assertNotIn("x", result.split("—")[0])  # No dimensions before dash
        self.assertIn("image/svg+xml", result)
        self.assertIn("512 bytes", result)

    def test_url_source(self):
        result = build_description("https://example.com/img.jpg", "image/jpeg", 2048, (100, 50))
        self.assertIn("https://example.com/img.jpg", result)
        self.assertIn("100x50", result)


class TestReadLocalBytes(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp.write(MINIMAL_PNG)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_read_png(self):
        data, mime = read_local_bytes(self.tmp.name)
        self.assertEqual(mime, "image/png")
        self.assertEqual(data, MINIMAL_PNG)

    def test_file_not_found(self):
        with self.assertRaises(FileNotFoundError):
            read_local_bytes("/nonexistent/path/image.png")

    def test_unsupported_format(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not an image")
            f.flush()
            try:
                with self.assertRaises(ValueError):
                    read_local_bytes(f.name)
            finally:
                os.unlink(f.name)


class TestReadRemoteBytes(unittest.TestCase):
    @patch("server.httpx.Client")
    def test_read_remote_png(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "image/png"}
        mock_response.content = MINIMAL_PNG
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        data, mime = read_remote_bytes("https://example.com/image.png")
        self.assertEqual(mime, "image/png")
        self.assertEqual(data, MINIMAL_PNG)

    @patch("server.httpx.Client")
    def test_fallback_to_extension_mime(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "application/octet-stream"}
        mock_response.content = MINIMAL_PNG
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        data, mime = read_remote_bytes("https://example.com/image.png")
        self.assertEqual(mime, "image/png")

    @patch("server.httpx.Client")
    def test_http_error(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        with self.assertRaises(httpx.HTTPStatusError):
            read_remote_bytes("https://example.com/missing.png")

    @patch("server.httpx.Client")
    def test_content_type_with_charset(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.headers = {"content-type": "image/jpeg; charset=utf-8"}
        mock_response.content = b"\xff\xd8\xff"
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.get.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        _, mime = read_remote_bytes("https://example.com/photo.jpg")
        self.assertEqual(mime, "image/jpeg")


class TestReadImageTool(unittest.TestCase):
    """Test the read_image MCP tool returns proper content format."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp.write(MINIMAL_PNG)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_returns_content_list(self):
        result = read_image(self.tmp.name)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)

    def test_first_element_is_text(self):
        result = read_image(self.tmp.name)
        self.assertIsInstance(result[0], TextContent)
        self.assertEqual(result[0].type, "text")
        self.assertIn("1x1", result[0].text)  # Resolution
        self.assertIn("image/png", result[0].text)

    def test_second_element_is_image(self):
        result = read_image(self.tmp.name)
        self.assertIsInstance(result[1], ImageContent)
        self.assertEqual(result[1].type, "image")
        self.assertEqual(result[1].mimeType, "image/png")
        decoded = base64.b64decode(result[1].data)
        self.assertEqual(decoded, MINIMAL_PNG)

    def test_file_not_found_returns_error_text(self):
        result = read_image("/nonexistent/image.png")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)
        self.assertIn("Error:", result[0].text)

    def test_unsupported_format_returns_error(self):
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"%PDF-1.4")
            f.flush()
            try:
                result = read_image(f.name)
                self.assertEqual(len(result), 1)
                self.assertIn("Unsupported", result[0].text)
            finally:
                os.unlink(f.name)

    @patch("server.read_remote_bytes")
    def test_url_dispatches_to_remote(self, mock_remote):
        mock_remote.return_value = (MINIMAL_PNG, "image/png")
        result = read_image("https://example.com/image.png")
        mock_remote.assert_called_once_with("https://example.com/image.png")
        self.assertEqual(len(result), 2)
        self.assertIsInstance(result[1], ImageContent)

    @patch("server.read_remote_bytes")
    def test_url_http_error(self, mock_remote):
        mock_remote.side_effect = httpx.HTTPStatusError(
            "500", request=MagicMock(), response=MagicMock(status_code=500)
        )
        result = read_image("https://example.com/broken.png")
        self.assertEqual(len(result), 1)
        self.assertIn("Error:", result[0].text)
        self.assertIn("500", result[0].text)

    def test_description_includes_resolution(self):
        result = read_image(self.tmp.name)
        self.assertIn("1x1", result[0].text)

    def test_description_includes_size(self):
        result = read_image(self.tmp.name)
        self.assertIn(f"{len(MINIMAL_PNG)} bytes", result[0].text)


class TestGetImageInfoTool(unittest.TestCase):
    """Test the get_image_info MCP tool."""

    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
        self.tmp.write(MINIMAL_PNG)
        self.tmp.close()

    def tearDown(self):
        os.unlink(self.tmp.name)

    def test_local_info_returns_text_content(self):
        result = get_image_info(self.tmp.name)
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 1)
        self.assertIsInstance(result[0], TextContent)

    def test_local_info_content(self):
        result = get_image_info(self.tmp.name)
        text = result[0].text
        self.assertIn("source: local", text)
        self.assertIn("mime_type: image/png", text)
        self.assertIn(f"size_bytes: {len(MINIMAL_PNG)}", text)
        self.assertIn("resolution: 1x1", text)

    def test_local_not_found(self):
        result = get_image_info("/nonexistent/image.png")
        self.assertIn("Error:", result[0].text)

    @patch("server.httpx.Client")
    def test_remote_info(self, mock_client_cls):
        mock_head_response = MagicMock()
        mock_head_response.headers = {
            "content-type": "image/jpeg",
            "content-length": "12345",
        }
        mock_head_response.raise_for_status = MagicMock()

        mock_get_response = MagicMock()
        mock_get_response.content = MINIMAL_PNG
        mock_get_response.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.head.return_value = mock_head_response
        mock_client.get.return_value = mock_get_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = get_image_info("https://example.com/photo.jpg")
        text = result[0].text
        self.assertIn("source: url", text)
        self.assertIn("mime_type: image/jpeg", text)
        self.assertIn("size_bytes: 12345", text)
        self.assertIn("resolution: 1x1", text)

    @patch("server.httpx.Client")
    def test_remote_http_error(self, mock_client_cls):
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "404", request=MagicMock(), response=MagicMock(status_code=404)
        )

        mock_client = MagicMock()
        mock_client.head.return_value = mock_response
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = get_image_info("https://example.com/missing.jpg")
        self.assertIn("Error:", result[0].text)


class TestMultipleFormats(unittest.TestCase):
    """Test that various image format extensions are handled correctly."""

    def _test_format(self, extension, expected_mime):
        data = b"\x00" * 16
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as f:
            f.write(data)
            f.flush()
            try:
                raw, mime = read_local_bytes(f.name)
                self.assertEqual(mime, expected_mime)
            finally:
                os.unlink(f.name)

    def test_png(self):
        self._test_format(".png", "image/png")

    def test_jpg(self):
        self._test_format(".jpg", "image/jpeg")

    def test_jpeg(self):
        self._test_format(".jpeg", "image/jpeg")

    def test_webp(self):
        self._test_format(".webp", "image/webp")

    def test_gif(self):
        self._test_format(".gif", "image/gif")

    def test_bmp(self):
        self._test_format(".bmp", "image/bmp")

    def test_tiff(self):
        self._test_format(".tiff", "image/tiff")

    def test_tif(self):
        self._test_format(".tif", "image/tiff")

    def test_svg(self):
        self._test_format(".svg", "image/svg+xml")

    def test_ico(self):
        self._test_format(".ico", "image/x-icon")


if __name__ == "__main__":
    unittest.main()
