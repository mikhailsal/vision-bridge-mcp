# Vision Bridge MCP

A lightweight MCP server that reads local and remote images and returns them for LLM vision consumption.

## Supported Formats

PNG, JPEG, WebP, GIF, BMP, TIFF, SVG, ICO

## Tools

| Tool | Description |
|------|-------------|
| `read_image` | Read an image (local path or URL) and return image content with metadata |
| `get_image_info` | Get image metadata (MIME type, size, resolution) without the full payload |

Both tools accept `await_for_seconds` and default to waiting up to `3.0` seconds for a local file or remote URL to become available. Fractional seconds are supported. Remote checks use increasing retry intervals and always make a final request at the deadline before returning an error.

## Installation

```bash
pip install -r requirements.txt
```

## Usage

### As MCP Server

```bash
python server.py
```

### MCP Config

```json
{
  "vision-bridge": {
    "command": "python",
    "args": ["/path/to/vision-bridge-mcp/server.py"]
  }
}
```

### Examples with MCP Wrapper

```bash
# Read a local image
./mcp_script_wrapper.sh --server vision-bridge read_image path=/path/to/image.png

# Read a remote image
./mcp_script_wrapper.sh --server vision-bridge read_image path=https://example.com/photo.jpg

# Read a local image and wait up to 0.5 seconds for it to appear
./mcp_script_wrapper.sh --server vision-bridge read_image path=/path/to/image.png await_for_seconds=0.5

# Get image info only
./mcp_script_wrapper.sh --server vision-bridge get_image_info path=/path/to/image.png

# Get remote image info and wait up to 10 seconds before failing
./mcp_script_wrapper.sh --server vision-bridge get_image_info path=https://example.com/photo.jpg await_for_seconds=10
```
