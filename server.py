#!/usr/bin/env python3
"""
Scrapling MCP Server - HTTP/SSE Transport Wrapper
Exposes scrapling-fetch-mcp as a Streamable HTTP endpoint for Coolify deployment
"""

import asyncio
import json
import os
import subprocess
import sys
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import StreamingResponse, JSONResponse
from starlette.middleware.cors import CORSMiddleware
import httpx

app = FastAPI(title="Scrapling MCP Server", version="1.0.0")

# CORS - allow all origins for MCP clients
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Global state
mcp_process: Optional[subprocess.Popen] = None
request_queue: asyncio.Queue = asyncio.Queue()
response_queues: dict[str, asyncio.Queue] = {}


def get_mcp_command():
    """Get the MCP server command based on available packages."""
    # Try scrapling-fetch-mcp first
    try:
        import scrapling_fetch_mcp

        return [sys.executable, "-m", "scrapling_fetch_mcp"]
    except ImportError:
        pass

    # Fallback: use the entry point directly
    return ["scrapling-fetch-mcp"]


@app.on_event("startup")
async def startup_event():
    """Start the MCP stdio server on startup."""
    global mcp_process

    cmd = get_mcp_command()
    print(f"Starting MCP server: {' '.join(cmd)}", file=sys.stderr)

    try:
        mcp_process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,  # Line buffered
        )

        # Start background tasks to handle I/O
        asyncio.create_task(read_mcp_output())
        asyncio.create_task(read_mcp_errors())

        print("MCP server started successfully", file=sys.stderr)
    except Exception as e:
        print(f"Failed to start MCP server: {e}", file=sys.stderr)
        raise


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup MCP process on shutdown."""
    global mcp_process
    if mcp_process:
        mcp_process.terminate()
        try:
            mcp_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mcp_process.kill()
        print("MCP server stopped", file=sys.stderr)


async def read_mcp_output():
    """Read MCP server stdout and route responses to waiting requests."""
    global mcp_process
    while mcp_process and mcp_process.poll() is None:
        try:
            line = await asyncio.get_event_loop().run_in_executor(
                None, mcp_process.stdout.readline
            )
            if not line:
                await asyncio.sleep(0.01)
                continue

            try:
                data = json.loads(line)
                request_id = data.get("id")
                if request_id and request_id in response_queues:
                    await response_queues[request_id].put(data)
                else:
                    # Broadcast or log
                    print(f"MCP message: {line.strip()}", file=sys.stderr)
            except json.JSONDecodeError:
                print(f"MCP stdout: {line.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"Error reading MCP output: {e}", file=sys.stderr)
            await asyncio.sleep(0.1)


async def read_mcp_errors():
    """Read MCP server stderr for logging."""
    global mcp_process
    while mcp_process and mcp_process.poll() is None:
        try:
            line = await asyncio.get_event_loop().run_in_executor(
                None, mcp_process.stderr.readline
            )
            if line:
                print(f"MCP stderr: {line.strip()}", file=sys.stderr)
        except Exception as e:
            print(f"Error reading MCP stderr: {e}", file=sys.stderr)
            await asyncio.sleep(0.1)


@app.get("/health")
async def health_check():
    """Health check endpoint."""
    if mcp_process and mcp_process.poll() is None:
        return {"status": "healthy", "mcp": "running"}
    return JSONResponse(
        status_code=503, content={"status": "unhealthy", "mcp": "not running"}
    )


@app.get("/")
async def root():
    """Root endpoint with basic info."""
    return {
        "name": "Scrapling MCP Server",
        "version": "1.0.0",
        "endpoints": {
            "mcp_post": "POST /mcp",
            "mcp_stream": "GET /mcp/stream",
            "health": "GET /health",
        },
    }


@app.post("/mcp")
async def mcp_post(request: Request):
    """
    Handle MCP JSON-RPC requests via POST.
    This is the primary endpoint for MCP tool invocations.
    """
    global mcp_process

    if not mcp_process or mcp_process.poll() is not None:
        raise HTTPException(status_code=503, detail="MCP server not running")

    try:
        payload = await request.json()
        request_id = payload.get("id", str(asyncio.get_event_loop().time()))

        # Create response queue for this request
        response_queues[request_id] = asyncio.Queue()

        # Send to MCP process
        message = json.dumps(payload) + "\n"
        mcp_process.stdin.write(message)
        mcp_process.stdin.flush()

        # Wait for response with timeout
        try:
            response = await asyncio.wait_for(
                response_queues[request_id].get(), timeout=60.0
            )
            return response
        except asyncio.TimeoutError:
            raise HTTPException(status_code=504, detail="MCP request timeout")
        finally:
            del response_queues[request_id]

    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"MCP error: {str(e)}")


@app.get("/mcp/stream")
async def mcp_stream():
    """
    SSE endpoint for streaming MCP responses.
    Clients can connect here to receive real-time updates.
    """
    global mcp_process

    if not mcp_process or mcp_process.poll() is not None:
        raise HTTPException(status_code=503, detail="MCP server not running")

    async def event_generator():
        """Generate SSE events from MCP output."""
        while mcp_process and mcp_process.poll() is None:
            try:
                # Check for any new responses (for broadcast or notifications)
                await asyncio.sleep(0.1)
                yield f"data: {json.dumps({'type': 'heartbeat', 'timestamp': asyncio.get_event_loop().time()})}\n\n"
            except Exception as e:
                yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
                break

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )


@app.post("/scrape")
async def scrape_endpoint(request: Request):
    """
    Simple REST endpoint for scraping - easier to test than full MCP.
    """
    try:
        data = await request.json()
        url = data.get("url")
        mode = data.get("mode", "html")  # html, markdown, text

        if not url:
            raise HTTPException(status_code=400, detail="URL is required")

        # Create MCP request for scraping
        mcp_request = {
            "jsonrpc": "2.0",
            "id": str(asyncio.get_event_loop().time()),
            "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"url": url, "mode": mode}},
        }

        # Send to MCP and get response
        request_id = mcp_request["id"]
        response_queues[request_id] = asyncio.Queue()

        message = json.dumps(mcp_request) + "\n"
        mcp_process.stdin.write(message)
        mcp_process.stdin.flush()

        response = await asyncio.wait_for(
            response_queues[request_id].get(), timeout=60.0
        )

        del response_queues[request_id]

        if "error" in response:
            raise HTTPException(status_code=500, detail=response["error"])

        return response.get("result", {})

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Scrape error: {str(e)}")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
