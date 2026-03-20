from fastmcp import FastMCP
import os
import sys
import time
from typing import List, Dict, Any, Optional
from postgres_memory_api import PostgresMemoryAPI
from ollama_embeddings import OllamaEmbeddings

# Get server name from environment or use default
server_name = os.environ.get("MCP_SERVER_NAME", "Local Context Memory")

# Initialize the MCP server
mcp = FastMCP(name=server_name)

# Check if Ollama is available
ollama_available = False
ollama_url = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")
embedding_model = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:v1.5")
keep_alive = os.environ.get("OLLAMA_KEEP_ALIVE", "10m")
ollama_embeddings = None

try:
    import requests
    response = requests.get(f"{ollama_url}/api/tags")
    if response.status_code == 200:
        models = response.json().get("models", [])
        model_names = [model.get("name", "") for model in models]

        if embedding_model in model_names:
            ollama_available = True
            ollama_embeddings = OllamaEmbeddings(
                model_name=embedding_model,
                base_url=ollama_url,
                keep_alive=keep_alive
            )
        else:
            print(f"Ollama found but embedding model {embedding_model} not available, using text search only", file=sys.stderr)
    else:
        print("Ollama API returned error, using text search only", file=sys.stderr)
except Exception as e:
    print(f"Ollama check failed: {e}, using text search only", file=sys.stderr)

# Initialize the PostgreSQL memory API
memory_api = PostgresMemoryAPI(ollama_embeddings=ollama_embeddings)


# ── Agency-Agents compatible tools ──────────────────────────────────────────

@mcp.tool
def remember(content: str, tags: Optional[List[str]] = None,
             domain: Optional[str] = None, source: Optional[str] = None,
             importance: Optional[float] = None,
             ttl_seconds: Optional[int] = None) -> str:
    """
    Store a decision, deliverable, or context snapshot with tags for later recall.

    Use this whenever you make a key decision, complete a deliverable, or want to
    preserve context for future sessions or other agents. For risky multi-step changes,
    create a checkpoint first so you can rollback atomically if needed.

    Parameters:
    - content (str): What to remember. Include enough context that a future session
                    or a different agent can understand what was done and why.
    - tags (List[str], optional): Tags for organizing and finding this memory later.
                                  Use agent name, project name, and topic as tags.
                                  Examples: ["backend-architect", "retroboard", "api-spec"]
    - domain (str, optional): Memory domain for segmentation (default: 'default').
    - source (str, optional): Where this memory originated from.
    - importance (float, optional): Importance score from 0.0 to 1.0.
    - ttl_seconds (int, optional): Time-to-live in seconds. Memory auto-expires after this
                                   duration. Use for temporal info (meetings, sprint status).
                                   Omit for permanent memories.

    Returns:
    str: A unique memory ID for referencing this memory later.
    """
    metadata = {}
    if source:
        metadata["source"] = source
    if importance is not None:
        metadata["importance"] = importance

    memory_id = memory_api.store_memory(content, metadata, domain, tags=tags, ttl_seconds=ttl_seconds)
    return memory_id


@mcp.tool
def recall(tags: Optional[List[str]] = None, query: Optional[str] = None,
           domain: Optional[str] = None,
           limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Search for relevant memories by tag, keyword, or semantic similarity.

    Use this at the start of a session to pick up context from previous sessions,
    or when you need to find what a specific agent produced.

    Parameters:
    - tags (List[str], optional): Filter to memories matching ALL of these tags.
                                  Examples: ["backend-architect", "retroboard"]
    - query (str, optional): Search query for semantic or text matching.
    - domain (str, optional): Domain to search within (default: 'default').
    - limit (int, optional): Maximum number of memories to return (default: 5).

    Returns:
    List[Dict]: Matching memories with id, content, tags, metadata, and score.
    """
    results = memory_api.retrieve_memories(
        query=query or "", limit=limit, domain=domain, tags=tags
    )
    for result in results:
        if "score" not in result:
            result["score"] = 0.0
    return results


@mcp.tool
def checkpoint(name: str, tags: Optional[List[str]] = None,
               domain: Optional[str] = None) -> str:
    """
    Create a named checkpoint before risky work.

    Use this before making a series of changes that might need to be undone
    atomically. If something goes wrong, use rollback with the checkpoint ID
    to undo all changes made after this point.

    Parameters:
    - name (str): Human-readable name for this checkpoint.
                  Examples: "before-api-redesign", "pre-schema-migration"
    - tags (List[str], optional): Tags for organizing checkpoints.
    - domain (str, optional): Domain for this checkpoint (default: 'default').

    Returns:
    str: A checkpoint ID to use with rollback.
    """
    return memory_api.create_checkpoint(name, domain, tags=tags)


@mcp.tool
def rollback(checkpoint_id: str, domain: Optional[str] = None) -> bool:
    """
    Revert to a previous checkpoint, atomically undoing all changes made after it.

    This deletes memories created after the checkpoint and restores memories
    that were updated after it to their checkpoint-time state.

    Use this when a QA check fails or a decision turns out wrong. Instead of
    manually undoing changes one by one, roll back to the last known-good state.

    Parameters:
    - checkpoint_id (str): The checkpoint ID returned by the checkpoint tool.
    - domain (str, optional): The domain for this checkpoint.

    Returns:
    bool: True if rollback succeeded, False if checkpoint not found.
    """
    return memory_api.rollback_to_checkpoint(checkpoint_id, domain)


@mcp.tool
def rollback_memory(memory_id: str, domain: Optional[str] = None) -> bool:
    """
    Revert a single memory to its previous version.

    For surgical single-memory rollback. Each call pops one version from the
    history stack. Use the checkpoint-based rollback tool for multi-memory recovery.

    Parameters:
    - memory_id (str): The ID of the memory to roll back.
    - domain (str, optional): The domain where this memory is stored.

    Returns:
    bool: True if rollback succeeded, False if no previous version exists.
    """
    return memory_api.rollback_memory(memory_id, domain)


@mcp.tool
def list_checkpoints(domain: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List all available checkpoints for a domain, newest first.

    Parameters:
    - domain (str, optional): Domain to list checkpoints for (default: 'default').

    Returns:
    List[Dict]: Checkpoints with id, name, tags, and created_at.
    """
    return memory_api.list_checkpoints(domain)


@mcp.tool
def purge_expired(domain: Optional[str] = None) -> int:
    """
    Delete all expired memories (those past their TTL) in a domain.

    Parameters:
    - domain (str, optional): Domain to purge (default: 'default').

    Returns:
    int: Number of memories deleted.
    """
    return memory_api.purge_expired(domain)


@mcp.tool
def consolidate_memories(tags: List[str], older_than_days: Optional[int] = 30,
                         domain: Optional[str] = None) -> List[str]:
    """
    Compress old memories matching tags into LLM-generated summaries.

    The originals are deleted and replaced with summaries that preserve key
    decisions, constraints, and outcomes. Use at project milestones to reduce noise.

    This is lossy compression — granular details (exact URLs, error codes) may not
    survive the summary. For permanently critical memories, do not consolidate.

    Parameters:
    - tags (List[str]): Filter to memories matching ALL of these tags.
    - older_than_days (int, optional): Only consolidate memories older than this (default: 30).
    - domain (str, optional): Domain to consolidate within (default: 'default').

    Returns:
    List[str]: IDs of new summary memories, or empty list if too few memories to consolidate.
    """
    if not ollama_available:
        return []

    def _summarize(text: str) -> str:  # pragma: no cover
        import requests as req
        response = req.post(
            f"{ollama_url}/api/generate",
            json={
                "model": os.environ.get("OLLAMA_CHAT_MODEL", "llama3"),
                "prompt": f"Summarize these memories into a concise summary preserving key decisions, constraints, and outcomes:\n\n{text}\n\nSummary:",
                "stream": False,
            },
            timeout=120,
        )
        response.raise_for_status()
        return response.json()["response"]

    return memory_api.consolidate_memories(tags, _summarize, older_than_days=older_than_days, domain=domain)


@mcp.tool
def search(query: str, domain: Optional[str] = None,
           limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Find specific memories across sessions and agents using semantic or text search.

    Unlike recall (which filters by tags), search performs broad content-based
    retrieval across all memories in a domain.

    Parameters:
    - query (str): The search query.
    - domain (str, optional): Domain to search within (default: 'default').
    - limit (int, optional): Maximum results to return (default: 5).

    Returns:
    List[Dict]: Matching memories with id, content, tags, metadata, score, and query.
    """
    results = memory_api.retrieve_memories(query, limit, domain)
    for result in results:
        result["query"] = query
        if "score" not in result:
            result["score"] = 0.0
    return results


# ── Legacy tools (backward compatibility) ───────────────────────────────────

@mcp.tool
def store_memory(content: str, domain: Optional[str] = None,
                 source: Optional[str] = None, importance: Optional[float] = None) -> str:
    """
    Store a new memory chunk in the persistent memory system.
    (Legacy interface — prefer 'remember' for new usage.)

    Parameters:
    - content (str): The text content to remember.
    - domain (str, optional): The domain/context for this memory.
    - source (str, optional): Where this memory originated from.
    - importance (float, optional): Importance score from 0.0 to 1.0.

    Returns:
    str: A unique memory ID.
    """
    metadata = {}
    if source:
        metadata["source"] = source
    if importance is not None:
        metadata["importance"] = importance

    memory_id = memory_api.store_memory(content, metadata, domain)
    return memory_id


@mcp.tool
def update_memory(memory_id: str, content: Optional[str] = None,
                  importance: Optional[float] = None, domain: Optional[str] = None) -> bool:
    """
    Update an existing memory chunk with new information.
    (Legacy interface.)

    Parameters:
    - memory_id (str): The unique ID of the memory to update.
    - content (str, optional): New content to replace existing content.
    - importance (float, optional): New importance score from 0.0 to 1.0.
    - domain (str, optional): The domain where this memory is stored.

    Returns:
    bool: True if successful, False if memory_id not found.
    """
    metadata = {}
    if importance is not None:
        metadata["importance"] = importance

    success = memory_api.update_memory(memory_id, content, metadata, domain)
    return success


@mcp.tool
def search_memories(query: str, domain: Optional[str] = None,
                    limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Search for memories within a specific domain.
    (Legacy interface — prefer 'search' for new usage.)

    Parameters:
    - query (str): The search query.
    - domain (str, optional): Domain to search within.
    - limit (int, optional): Maximum results (default: 5).

    Returns:
    List[Dict]: Matching memories with search metadata.
    """
    results = memory_api.retrieve_memories(query, limit, domain)
    for result in results:
        result["query"] = query
        if "score" not in result:
            result["score"] = 0.0
    return results


@mcp.tool
def list_memory_domains() -> List[str]:
    """
    List all available memory domains in the database.

    Returns:
    List[str]: Domain names that can be used with remember, recall, and search.
    """
    return memory_api.list_domains()


@mcp.resource("memory://{domain}/{query}")
def get_memories(domain: str, query: str, limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Retrieve memories from a specific domain using semantic or text search.

    URI Pattern: memory://{domain}/{query}

    Parameters:
    - domain (str): The domain to search within.
    - query (str): The search query.
    - limit (int, optional): Maximum memories to return (default: 5).

    Returns:
    List[Dict]: Matching memory objects.
    """
    results = memory_api.retrieve_memories(query, limit, domain)
    return results


@mcp.prompt
def summarize_memories(memories: List[Dict[str, Any]]) -> str:
    """
    Create a prompt for summarizing a list of memories.

    Parameters:
    - memories: List of memory chunks to summarize

    Returns:
    - A prompt for the LLM to create a summary
    """
    memory_texts = [f"Memory {i+1}: {mem['content']}" for i, mem in enumerate(memories)]
    formatted_memories = "\n".join(memory_texts)

    prompt = f"""Below are several memory chunks related to a user's interests and history.
Please create a concise summary that captures the key points and patterns:

{formatted_memories}

Summary:"""

    return prompt

if __name__ == "__main__":  # pragma: no cover
    mcp.run()  # Start the FastMCP server
