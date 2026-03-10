# Vision Bridge MCP

A lightweight MCP server that reads local and remote images and returns them for LLM vision consumption.

## Supported Formats

PNG, JPEG, WebP, GIF, BMP, TIFF, SVG, ICO

## Tools

| Tool | Description |
|------|-------------|
| `read_image` | Read an image (local or URL) and return Base64 with metadata |
| `read_image_base64` | Read an image and return only the raw Base64 string |
| `get_image_info` | Get image metadata (MIME type, size) without the full payload |

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

# Get image info only
./mcp_script_wrapper.sh --server vision-bridge get_image_info path=/path/to/image.png
```
