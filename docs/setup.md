# Setup Guide

Set up the Local Context Memory MCP server with a self-built Docker image, client-side Ollama, and Claude Code integration.

## Prerequisites

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) installed and running
- [Ollama](https://ollama.com/download) installed on your machine
- [Claude Code](https://claude.ai/code) CLI installed

## Step 1: Install and configure Ollama

Ollama runs on your machine and provides the embedding model for semantic search.

**Windows:**
Download and run the installer from https://ollama.com/download

**Linux (Arch, Ubuntu, etc.):**
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

Pull the embedding model:
```bash
ollama pull nomic-embed-text:v1.5
```

Verify Ollama is running:
```bash
curl http://localhost:11434/api/tags
```

You should see `nomic-embed-text:v1.5` in the model list.

## Step 2: Build the Docker image

From the project root:

```bash
docker build -f Dockerfile.postgres_version -t local-memory-mcp:postgres .
```

This builds a self-contained image with PostgreSQL + pgvector + the MCP server. Takes a few minutes on first build.

## Step 3: Test the container

**Windows (PowerShell):**
```powershell
docker run --rm -i -v ${PWD}/postgres-data:/var/lib/postgresql/data -e OLLAMA_API_URL=http://host.docker.internal:11434 local-memory-mcp:postgres
```

**Windows (CMD):**
```cmd
docker run --rm -i -v %cd%/postgres-data:/var/lib/postgresql/data -e OLLAMA_API_URL=http://host.docker.internal:11434 local-memory-mcp:postgres
```

**Linux / macOS:**
```bash
docker run --rm -i \
  -v ./postgres-data:/var/lib/postgresql/data \
  -e OLLAMA_API_URL=http://host.docker.internal:11434 \
  local-memory-mcp:postgres
```

**Linux note:** `host.docker.internal` may not resolve. Use the Docker bridge gateway IP instead:
```bash
# Find your Docker bridge IP
ip addr show docker0 | grep inet
# Usually 172.17.0.1

docker run --rm -i \
  -v ./postgres-data:/var/lib/postgresql/data \
  -e OLLAMA_API_URL=http://172.17.0.1:11434 \
  local-memory-mcp:postgres
```

The `-v` flag persists your database on the host. Your memories survive container restarts. Delete the `postgres-data` directory to start fresh.

Press `Ctrl+C` to stop the container once you see it's running.

## Step 4: Connect to Claude Code

Choose one of the two options below depending on whether you want the memory server available in a specific project or across all projects.

### Option A: Project-scoped (`.mcp.json`)

Create a `.mcp.json` file in your project root. This makes the memory server available only when Claude Code is running in this project directory.

**Windows:**
```json
{
  "mcpServers": {
    "memory": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "C:/Users/YOUR_USER/memory-data:/var/lib/postgresql/data",
        "-e", "OLLAMA_API_URL=http://host.docker.internal:11434",
        "local-memory-mcp:postgres"
      ]
    }
  }
}
```

**Linux / macOS:**
```json
{
  "mcpServers": {
    "memory": {
      "command": "docker",
      "args": [
        "run", "--rm", "-i",
        "-v", "/home/YOUR_USER/memory-data:/var/lib/postgresql/data",
        "-e", "OLLAMA_API_URL=http://172.17.0.1:11434",
        "local-memory-mcp:postgres"
      ]
    }
  }
}
```

Replace `YOUR_USER` and the volume path with where you want your memories stored. Use an absolute path.

### Option B: CLI-wide (available in all projects)

This registers the memory server globally so it's available in every Claude Code session.

**Windows (PowerShell):**
```powershell
claude mcp add --scope user --transport stdio memory -- docker run --rm -i -v C:/Users/YOUR_USER/memory-data:/var/lib/postgresql/data -e OLLAMA_API_URL=http://host.docker.internal:11434 local-memory-mcp:postgres
```

**Windows (CMD):**
```cmd
claude mcp add --scope user --transport stdio memory -- docker run --rm -i -v C:\Users\YOUR_USER\memory-data:/var/lib/postgresql/data -e OLLAMA_API_URL=http://host.docker.internal:11434 local-memory-mcp:postgres
```

**Linux / macOS:**
```bash
claude mcp add --scope user --transport stdio memory -- \
  docker run --rm -i \
  -v /home/YOUR_USER/memory-data:/var/lib/postgresql/data \
  -e OLLAMA_API_URL=http://172.17.0.1:11434 \
  local-memory-mcp:postgres
```

To verify it was added:
```bash
claude mcp list
```

To remove it later:
```bash
claude mcp remove memory
```

## Step 5: Verify everything works

Start a new Claude Code session and check the MCP connection:
```
/mcp
```

You should see the `memory` server listed with all tools available (`remember`, `recall`, `checkpoint`, `rollback`, `search`, etc.).

Test it:
```
Use the remember tool to store "setup test" with tags ["test"]
```

Then:
```
Use the recall tool with tags ["test"]
```

If you get the memory back, the setup is complete.

## Troubleshooting

### "Ollama found but embedding model not available"
You need to pull the model:
```bash
ollama pull nomic-embed-text:v1.5
```

### Container can't reach Ollama
- **Windows/macOS**: Use `http://host.docker.internal:11434`
- **Linux**: Use your Docker bridge IP (usually `http://172.17.0.1:11434`)
- Verify Ollama is running: `curl http://localhost:11434/api/tags`

### Memories lost after restart
Make sure you're using the `-v` volume flag with an absolute path. The data lives on your host machine, not inside the container.

### "No module named ..." or Python errors
You're running the local Python path, not Docker. Either switch to Docker (recommended) or set up a conda environment:
```bash
conda create -p .venv python=3.12 -y
.venv/python -m pip install -r requirements.pgvector.txt
```

### Semantic search not working but everything else is
Ollama is unreachable or the model isn't pulled. The server gracefully falls back to text search. Fix Ollama and new memories will get embeddings. Existing memories without embeddings can be re-embedded by exporting and re-importing:
```
Use export_memories, then import_memories with the same data on the fixed instance
```
