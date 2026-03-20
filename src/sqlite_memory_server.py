from fastmcp import FastMCP
import os
import time
from typing import List, Dict, Any, Optional
from sqlite_memory_api import SQLiteMemoryAPI
from sqlite_vector_api import FAISSVectorAPI
from ollama_embeddings import OllamaEmbeddings

# Get server name from environment or use default
server_name = os.environ.get("MCP_SERVER_NAME", "Local Context Memory")
data_dir = os.environ.get("MCP_DATA_DIR", ".")

# Initialize the MCP server
mcp = FastMCP(name=server_name)

# Check if Ollama is available
ollama_available = False
ollama_url = os.environ.get("OLLAMA_API_URL", "http://localhost:11434")
embedding_model = os.environ.get("OLLAMA_EMBEDDING_MODEL", "nomic-embed-text:v1.5")

try:
    import requests
    response = requests.get(f"{ollama_url}/api/tags")
    if response.status_code == 200:
        models = response.json().get("models", [])
        model_names = [model.get("name", "") for model in models]

        if embedding_model in model_names:
            ollama_available = True
        else:
            # Ollama found but embedding model not available, using text search only
            pass
    else:
        # Ollama API returned error, using text search only
        pass
except Exception as e:
    # Ollama check failed, using text search only
    pass

# Initialize vector store if Ollama is available
vector_store = None
if ollama_available:
    try:
        vector_store = FAISSVectorAPI(
            data_dir=data_dir,
            embedding_model=embedding_model,
            ollama_url=ollama_url
        )
    except Exception as e:
        # Failed to initialize vector store, using text search only
        pass
else:
    # Vector search not available, using text search only
    pass

# Initialize the SQLite memory API
memory_api = SQLiteMemoryAPI(vector_store=vector_store)


# ── Agency-Agents compatible tools ──────────────────────────────────────────

@mcp.tool
def remember(content: str, tags: Optional[List[str]] = None,
             source: Optional[str] = None, importance: Optional[float] = None,
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

    memory_id = memory_api.store_memory(content, metadata, tags=tags, ttl_seconds=ttl_seconds)
    return memory_id


@mcp.tool
def recall(tags: Optional[List[str]] = None, query: Optional[str] = None,
           limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Search for relevant memories by tag, keyword, or semantic similarity.

    Use this at the start of a session to pick up context from previous sessions,
    or when you need to find what a specific agent produced.

    Parameters:
    - tags (List[str], optional): Filter to memories matching ALL of these tags.
                                  Examples: ["backend-architect", "retroboard"]
    - query (str, optional): Search query for semantic or text matching.
    - limit (int, optional): Maximum number of memories to return (default: 5).

    Returns:
    List[Dict]: Matching memories with id, content, tags, metadata, and score.
    """
    results = memory_api.retrieve_memories(
        query=query or "", limit=limit, use_vector=bool(query), tags=tags
    )
    for result in results:
        if "score" not in result:
            result["score"] = 0.0
    return results


@mcp.tool
def checkpoint(name: str, tags: Optional[List[str]] = None) -> str:
    """
    Create a named checkpoint before risky work.

    Use this before making a series of changes that might need to be undone
    atomically. If something goes wrong, use rollback with the checkpoint ID
    to undo all changes made after this point.

    Parameters:
    - name (str): Human-readable name for this checkpoint.
                  Examples: "before-api-redesign", "pre-schema-migration"
    - tags (List[str], optional): Tags for organizing checkpoints.

    Returns:
    str: A checkpoint ID to use with rollback.
    """
    return memory_api.create_checkpoint(name, tags=tags)


@mcp.tool
def rollback(checkpoint_id: str) -> bool:
    """
    Revert to a previous checkpoint, atomically undoing all changes made after it.

    This deletes memories created after the checkpoint and restores memories
    that were updated after it to their checkpoint-time state.

    Use this when a QA check fails or a decision turns out wrong. Instead of
    manually undoing changes one by one, roll back to the last known-good state.

    Parameters:
    - checkpoint_id (str): The checkpoint ID returned by the checkpoint tool.

    Returns:
    bool: True if rollback succeeded, False if checkpoint not found.
    """
    return memory_api.rollback_to_checkpoint(checkpoint_id)


@mcp.tool
def rollback_memory(memory_id: str) -> bool:
    """
    Revert a single memory to its previous version.

    For surgical single-memory rollback. Each call pops one version from the
    history stack. Use the checkpoint-based rollback tool for multi-memory recovery.

    Parameters:
    - memory_id (str): The ID of the memory to roll back.

    Returns:
    bool: True if rollback succeeded, False if no previous version exists.
    """
    return memory_api.rollback_memory(memory_id)


@mcp.tool
def list_checkpoints() -> List[Dict[str, Any]]:
    """
    List all available checkpoints, newest first.

    Returns:
    List[Dict]: Checkpoints with id, name, tags, and created_at.
    """
    return memory_api.list_checkpoints()


@mcp.tool
def purge_expired() -> int:
    """
    Delete all expired memories (those past their TTL).

    Returns:
    int: Number of memories deleted.
    """
    return memory_api.purge_expired()


@mcp.tool
def consolidate_memories(tags: List[str], older_than_days: Optional[int] = 30) -> List[str]:
    """
    Compress old memories matching tags into LLM-generated summaries.

    The originals are deleted and replaced with summaries that preserve key
    decisions, constraints, and outcomes. Use at project milestones to reduce noise.

    This is lossy compression — granular details (exact URLs, error codes) may not
    survive the summary. For permanently critical memories, do not consolidate.

    Parameters:
    - tags (List[str]): Filter to memories matching ALL of these tags.
    - older_than_days (int, optional): Only consolidate memories older than this (default: 30).

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

    return memory_api.consolidate_memories(tags, _summarize, older_than_days=older_than_days)


@mcp.tool
def search(query: str, limit: Optional[int] = 5,
           use_vector: Optional[bool] = True) -> List[Dict[str, Any]]:
    """
    Find specific memories across sessions and agents using semantic or text search.

    Unlike recall (which filters by tags), search performs broad content-based
    retrieval across all memories.

    Parameters:
    - query (str): The search query.
    - limit (int, optional): Maximum results to return (default: 5).
    - use_vector (bool, optional): Use semantic search (default: True).

    Returns:
    List[Dict]: Matching memories with id, content, tags, metadata, score, and query.
    """
    results = memory_api.retrieve_memories(query, limit, use_vector)
    for result in results:
        result["query"] = query
        if "score" not in result:
            result["score"] = 0.0
    return results


# ── Legacy tools (backward compatibility) ───────────────────────────────────

@mcp.tool
def store_memory(content: str, source: Optional[str] = None,
                 importance: Optional[float] = None) -> str:
    """
    Store a new memory chunk in the persistent memory system.
    (Legacy interface — prefer 'remember' for new usage.)

    Parameters:
    - content (str): The text content to remember.
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

    memory_id = memory_api.store_memory(content, metadata)
    return memory_id


@mcp.tool
def update_memory(memory_id: str, content: Optional[str] = None,
                  importance: Optional[float] = None) -> bool:
    """
    Update an existing memory chunk with new information.
    (Legacy interface.)

    Parameters:
    - memory_id (str): The unique ID of the memory to update.
    - content (str, optional): New content to replace existing content.
    - importance (float, optional): New importance score from 0.0 to 1.0.

    Returns:
    bool: True if successful, False if memory_id not found.
    """
    metadata = {}
    if importance is not None:
        metadata["importance"] = importance

    success = memory_api.update_memory(memory_id, content, metadata)
    return success


@mcp.tool
def search_memories(query: str, limit: Optional[int] = 5,
                    use_vector: Optional[bool] = True) -> List[Dict[str, Any]]:
    """
    Search for memories using semantic search with optional fallback.
    (Legacy interface — prefer 'search' for new usage.)

    Parameters:
    - query (str): The search query.
    - limit (int, optional): Maximum results (default: 5).
    - use_vector (bool, optional): Use semantic vector search (default: True).

    Returns:
    List[Dict]: Matching memories with search metadata.
    """
    results = memory_api.retrieve_memories(query, limit, use_vector)
    for result in results:
        result["query"] = query
        if "score" not in result:
            result["score"] = 0.0
    return results


@mcp.resource("memory://{query}")
def get_memories(query: str, limit: Optional[int] = 5) -> List[Dict[str, Any]]:
    """
    Retrieve memories relevant to a search query using semantic search.

    URI Pattern: memory://{query}

    Parameters:
    - query (str): The search query.
    - limit (int, optional): Maximum memories to return (default: 5).

    Returns:
    List[Dict]: Matching memory objects.
    """
    results = memory_api.retrieve_memories(query, limit)
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
