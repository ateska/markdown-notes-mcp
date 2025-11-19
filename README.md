# markdown-notes-mcp

[![License: BSD 3-Clause](https://img.shields.io/badge/License-BSD%203--Clause-blue.svg)](LICENSE)

MCP server that enables AI assistants to manage Markdown notes through the Model Context Protocol.
Built on [ASAB](https://github.com/TeskaLabs/asab) framework with multi-tenant support and image upload capabilities.
This project is also a demo how to write MCP servers using ASAB framework and Python.


## Overview

`markdown-notes-mcp` is a [MCP](https://modelcontextprotocol.io/docs/getting-started/intro) (Model Context Protocol) server implementation that provides AI assistants with tools to create, read, update, delete, and organize Markdown notes.
The server exposes a standardized MCP interface, allowing AI applications to interact with a local or remote Markdown notes repository.
This MCP server ofers _Streamable HTTP transport_, it DOESN'T provide _Stdio transport_ (yet).

The cool part is that you can interconnect more LLMs to work colaboratively on Markdown notes; ie. one is the writer and second is the opponent.

The Model Context Protocol (MCP) is a standardized protocol for connecting AI applications to external data sources and tools. This implementation leverages ASAB's microservice framework (`aiohttp` internally), providing unified configuration, logging, metrics, and HTTP server capabilities.


## Features

- **Full CRUD Operations**: Create, read, update, and delete Markdown notes
- **Directory Support**: Organize notes in nested directory structures
- **Image Upload**: WIP Upload and manage images (JPG, PNG, GIF) alongside notes
- **Multi-Tenant Support**: Isolated note storage per tenant
- **Resource Templates**: Access notes via URI templates (`note://{path}` and `img://{path}`)
- **MCP Protocol Compliance**: Full support for MCP protocol specification
- **Built on ASAB**: Production-ready microservice framework with async/await support
- **Security**: Path normalization prevents directory traversal attacks


## Installation

```bash
pip install asab-mcp
```

Or clone the repository and install dependencies:

```bash
git clone <repository-url>
cd markdown-notes-mcp
pip install -r requirements.txt  # if available
```

## Quick Start


### Running the Server

```bash
python markdown-notes-mcp.py
```

The server will start on `http://localhost:8898` by default. You can configure the listen address and notes directory in your ASAB configuration file.


### Configuration

Create a configuration file (e.g., `etc/markdown-notes-mcp.conf`):

```ini
[general]
notes = notes

[web]
listen = 8898

[tenants]
ids = tenant1,tenant2
```

- `notes`: Directory path where Markdown notes will be stored (default: `notes`), the first subdirectory is a tenant.
- `listen`: HTTP server listen address (default: `8898`)
- `ids`: Comma-separated list of tenant IDs for multi-tenant support, at least one tenant must be provided




## License

This project is licensed under the BSD 3-Clause License - see the [LICENSE](LICENSE) file for details.


## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.


## References

- [ASAB Framework](https://github.com/TeskaLabs/asab) - The underlying microservice framework
- [asab-mcp](https://github.com/TeskaLabs/asab-mcp) - MCP server implementation for ASAB
- [Model Context Protocol Specification](https://modelcontextprotocol.io/) - Official MCP specification
