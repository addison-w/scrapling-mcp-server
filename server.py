#!/usr/bin/env python3
"""
Scrapling MCP Server - Streamable HTTP Transport using MCP SDK
Exposes scrapling-fetch-mcp tools via Streamable HTTP transport.
"""

import os
import sys

from starlette.requests import Request as StarletteRequest
from starlette.responses import JSONResponse

from mcp.server.fastmcp import FastMCP


async def fetch_with_scrapling(
    url: str, mode: str = "markdown", browser: str = "camoufox"
) -> str:
    """Fetch page content using scrapling."""
    try:
        from scrapling import StealthyFetcher
        from markdownify import markdownify as md

        page = await StealthyFetcher.async_fetch(url)
        html = page.html_content

        if mode == "markdown":
            return md(html)
        elif mode == "html":
            return html
        elif mode == "text":
            return page.text
        else:
            return md(html)
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"


# Create FastMCP instance.
# streamable_http_path defaults to "/mcp", so the streamable HTTP endpoint
# will be exposed at POST /mcp.
mcp = FastMCP("scrapling-fetch-mcp")


@mcp.tool()
async def s_fetch_page(
    url: str, mode: str = "markdown", browser: str = "camoufox"
) -> str:
    """
    Fetch a webpage and return its content as markdown, HTML, or text.

    Args:
        url: The URL to fetch
        mode: Output format ('markdown', 'html', or 'text')
        browser: Browser engine to use (currently only 'camoufox' is supported)

    Returns:
        The page content in the requested format
    """
    return await fetch_with_scrapling(url, mode, browser)


@mcp.tool()
async def fetch(url: str, mode: str = "markdown") -> str:
    """
    Fetch a webpage and return its content.

    Args:
        url: The URL to fetch
        mode: Output format ('markdown' or 'html')

    Returns:
        The page content
    """
    return await fetch_with_scrapling(url, mode)


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: StarletteRequest) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse({"status": "healthy", "mcp": "running"})


@mcp.custom_route("/", methods=["GET"])
async def root_info(request: StarletteRequest) -> JSONResponse:
    """Root endpoint with server info."""
    return JSONResponse(
        {
            "name": "Scrapling MCP Server",
            "version": "2.0.0",
            "transport": "streamable-http",
            "endpoints": {
                "mcp": "POST /mcp (streamable HTTP)",
                "health": "GET /health",
            },
        }
    )


# Exported ASGI app for uvicorn. FastMCP's streamable_http_app() returns a
# Starlette app whose lifespan starts the StreamableHTTPSessionManager, so
# we don't need a custom lifespan here.
app = mcp.streamable_http_app()


def main():
    """Main entry point (used for local/stdio testing)."""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Run Scrapling MCP server")
    parser.add_argument("--host", default="0.0.0.0", help="Host to bind to")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("PORT", 8000)),
        help="Port to listen on",
    )
    parser.add_argument(
        "--stdio", action="store_true", help="Run as stdio server (for testing)"
    )
    args = parser.parse_args()

    if args.stdio:
        mcp.run()
    else:
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
