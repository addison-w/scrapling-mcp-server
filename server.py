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

from fastapi import FastAPI, Request, HTTPException, Header
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
# Store for SSE clients
sse_clients: set[asyncio.Queue] = set()


def get_mcp_command():
    """Get the MCP server command based on available packages."""
    import shutil

    scrapling_cmd = shutil.which("scrapling-fetch-mcp")
    if scrapling_cmd:
        return [scrapling_cmd]

    return [sys.executable, "-m", "scrapling_fetch_mcp"]


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
            bufsize=1,
        )

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

                # Also broadcast to SSE clients
                for client_queue in list(sse_clients):
                    try:
                        await client_queue.put(data)
                    except:
                        pass

                if not request_id:
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
            "mcp": "GET/POST /mcp (SSE/JSON-RPC)",
            "health": "GET /health",
        },
    }


@app.api_route("/mcp", methods=["GET", "POST"])
async def mcp_endpoint(
    request: Request,
    accept: Optional[str] = Header(None),
):
    """
    Unified MCP endpoint supporting both SSE (GET) and JSON-RPC (POST).

    GET with Accept: text/event-stream -> SSE connection for server-sent events
    POST with JSON body -> JSON-RPC request/response
    """
    global mcp_process

    if not mcp_process or mcp_process.poll() is not None:
        raise HTTPException(status_code=503, detail="MCP server not running")

    # Check if this is an SSE request (GET with Accept: text/event-stream)
    if request.method == "GET" and accept and "text/event-stream" in accept:
        return await handle_sse(request)

    # Otherwise handle as JSON-RPC POST
    if request.method == "POST":
        return await handle_json_rpc(request)

    raise HTTPException(status_code=405, detail="Method not allowed")


async def handle_sse(request: Request):
    """Handle SSE connection for MCP."""
    client_queue: asyncio.Queue = asyncio.Queue()
    sse_clients.add(client_queue)

    # Send initial endpoint event with session ID
    session_id = str(asyncio.get_event_loop().time())

    async def event_generator():
        try:
            # Send initial endpoint information
            endpoint_url = str(request.url)
            yield f"event: endpoint\ndata: {endpoint_url}\n\n"

            # Keep connection alive and stream messages
            while mcp_process and mcp_process.poll() is None:
                try:
                    # Wait for messages with timeout
                    data = await asyncio.wait_for(client_queue.get(), timeout=30.0)
                    event_data = json.dumps(data)
                    yield f"event: message\ndata: {event_data}\n\n"
                except asyncio.TimeoutError:
                    # Send heartbeat
                    yield f"event: heartbeat\ndata: {json.dumps({'timestamp': asyncio.get_event_loop().time()})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            sse_clients.discard(client_queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "Content-Type": "text/event-stream",
        },
    )


async def handle_json_rpc(request: Request):
    """Handle JSON-RPC request via POST."""
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


@app.post("/scrape")
async def scrape_endpoint(request: Request):
    """Simple REST endpoint for scraping - easier to test than full MCP."""
    try:
        data = await request.json()
        url = data.get("url")
        mode = data.get("mode", "html")

        if not url:
            raise HTTPException(status_code=400, detail="URL is required")

        mcp_request = {
            "jsonrpc": "2.0",
            "id": str(asyncio.get_event_loop().time()),
            "method": "tools/call",
            "params": {"name": "fetch", "arguments": {"url": url, "mode": mode}},
        }

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
