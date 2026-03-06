# Scrapling MCP Server

A Streamable HTTP wrapper for [scrapling-fetch-mcp](https://github.com/cyberchitta/scrapling-fetch-mcp) that exposes it as an HTTP/SSE endpoint for deployment on Coolify or any container platform.

## Endpoints

- `GET /` - Server info
- `GET /health` - Health check
- `POST /mcp` - MCP JSON-RPC endpoint (primary)
- `GET /mcp/stream` - SSE streaming endpoint
- `POST /scrape` - Simple REST scrape endpoint

## Deployment on Coolify

1. Create a new Git-based application
2. Point to this repository
3. Use Dockerfile deployment
4. Expose port 8000
5. Coolify handles SSL/TLS automatically

## Environment Variables

- `PORT` - Server port (default: 8000)

## MCP Tools Available

- `fetch` - Fetch HTML/markdown from URLs using Scrapling

## Testing

```bash
# Health check
curl https://your-domain.com/health

# Simple scrape
curl -X POST https://your-domain.com/scrape \
  -H "Content-Type: application/json" \
  -d '{"url": "https://example.com", "mode": "markdown"}'

# MCP JSON-RPC
curl -X POST https://your-domain.com/mcp \
  -H "Content-Type: application/json" \
  -d '{
    "jsonrpc": "2.0",
    "id": "1",
    "method": "tools/call",
    "params": {
      "name": "fetch",
      "arguments": {"url": "https://example.com", "mode": "markdown"}
    }
  }'
```
