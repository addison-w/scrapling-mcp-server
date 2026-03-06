#!/usr/bin/env python3
"""
Scrapling MCP Server - HTTP/SSE Transport using MCP SDK
Exposes scrapling-fetch-mcp tools via SSE and HTTP transports
"""

import asyncio
import json
import os
import sys
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.applications import Starlette
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request as StarletteRequest
from starlette.responses import Response
from starlette.routing import Mount, Route
import uvicorn

# MCP imports
from mcp.server import Server
from mcp.server.fastmcp import FastMCP
from mcp.server.sse import SseServerTransport
from mcp.types import TextContent, Tool


async def fetch_with_scrapling(
    url: str, mode: str = "markdown", browser: str = "camoufox"
) -> str:
    """Fetch page content using scrapling."""
    try:
        from scrapling import StealthyFetcher

        fetcher = StealthyFetcher()
        page = fetcher.fetch(url)

        if mode == "markdown":
            return page.markdown
        elif mode == "html":
            return page.html
        elif mode == "text":
            return page.get_text(separator="\n", strip=True)
        else:
            return page.markdown
    except Exception as e:
        return f"Error fetching {url}: {str(e)}"


# Create FastMCP instance
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


# Create MCP server
mcp_server = mcp._mcp_server


def create_starlette_app(debug: bool = False) -> Starlette:
    """Create Starlette app with MCP SSE transport."""

    # SSE transport - messages endpoint for POST requests
    sse = SseServerTransport("/messages/")

    async def handle_sse(request: StarletteRequest) -> Response:
        """Handle SSE connection for MCP."""
        async with sse.connect_sse(
            request.scope,
            request.receive,
            request._send,
        ) as (read_stream, write_stream):
            await mcp_server.run(
                read_stream,
                write_stream,
                mcp_server.create_initialization_options(),
            )
            while True:
                await asyncio.sleep(1)

    async def health_check(request: StarletteRequest) -> JSONResponse:
        """Health check endpoint."""
        return JSONResponse({"status": "healthy", "mcp": "running"})

    async def root_info(request: StarletteRequest) -> JSONResponse:
        """Root endpoint with server info."""
        return JSONResponse(
            {
                "name": "Scrapling MCP Server",
                "version": "1.0.0",
                "endpoints": {
                    "sse": "GET /mcp",
                    "messages": "POST /messages/",
                    "health": "GET /health",
                },
            }
        )

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncIterator[None]:
        """Lifespan context manager for startup/shutdown."""
        print("Scrapling MCP server starting up...", file=sys.stderr)
        try:
            yield
        finally:
            print("Scrapling MCP server shutting down...", file=sys.stderr)

    return Starlette(
        debug=debug,
        routes=[
            Route("/", endpoint=root_info),
            Route("/health", endpoint=health_check),
            Route("/mcp", endpoint=handle_sse),  # SSE endpoint
            Mount("/messages/", app=sse.handle_post_message),  # POST endpoint
        ],
        lifespan=lifespan,
    )


# Create the Starlette app (exported for uvicorn)
app = create_starlette_app(
    debug=os.environ.get("DEBUG", "").lower() in ("true", "1", "yes")
)


def main():
    """Main entry point."""
    import argparse

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
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    args = parser.parse_args()

    if args.stdio:
        # Run as stdio server
        mcp.run()
    else:
        # Run as HTTP/SSE server
        app = create_starlette_app(debug=args.debug)
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
