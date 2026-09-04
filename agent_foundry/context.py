"""Layer 06 — Context Layer: working, episodic, semantic (RAG), procedural
memory, a knowledge graph, and user/org profiles — every chip in that layer
of the diagram.

InMemoryVectorStore is a keyword-overlap fallback so the framework runs with
zero external dependencies; swap it for a real embedding store (pgvector,
Pinecone, ...) by implementing the same VectorStore protocol — nothing else
in the framework changes.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Protocol

from .contracts import ToolSpec


# ---- Semantic memory / RAG --------------------------------------------------------

class VectorStore(Protocol):
    def upsert(self, thread_id: str, text: str, metadata: dict) -> None: ...
    def search(self, thread_id: str, query: str, k: int = 4) -> list[str]: ...


class InMemoryVectorStore:
    def __init__(self) -> None:
        self._docs: dict[str, list[tuple[str, dict]]] = {}

    def upsert(self, thread_id: str, text: str, metadata: dict) -> None:
        self._docs.setdefault(thread_id, []).append((text, metadata))

    def search(self, thread_id: str, query: str, k: int = 4) -> list[str]:
        terms = set(query.lower().split())
        scored = sorted(
            self._docs.get(thread_id, []),
            key=lambda d: len(terms & set(d[0].lower().split())),
            reverse=True,
        )
        return [text for text, _ in scored[:k]]


class ChromaVectorStore:
    """A real embedded vector database (ChromaDB) — proves VectorStore is
    genuinely swappable for *any* vector DB, not just InMemoryVectorStore's
    keyword-overlap fallback. One collection, thread_id as a metadata filter,
    matching InMemoryVectorStore's per-thread semantics exactly. Pass any
    ChromaDB-compatible embedding_function (OpenAI, Cohere, sentence-transformers,
    a custom one, or omit it for Chroma's own default) — this class doesn't
    hardcode an embedding provider, only the storage/query engine.
    Requires `pip install chromadb`. The same pattern (implement upsert/search)
    is exactly how Pinecone, Weaviate, Qdrant, or pgvector plug in instead."""

    def __init__(self, *, path: str | None = None, embedding_function: Any = None, collection_name: str = "agent_foundry") -> None:
        import chromadb

        client = chromadb.PersistentClient(path=path) if path else chromadb.EphemeralClient()
        kw = {"embedding_function": embedding_function} if embedding_function is not None else {}
        self._collection = client.get_or_create_collection(collection_name, **kw)
        self._next_id = 0

    def upsert(self, thread_id: str, text: str, metadata: dict) -> None:
        self._next_id += 1
        self._collection.add(ids=[f"{thread_id}-{self._next_id}"], documents=[text], metadatas=[{**metadata, "thread_id": thread_id}])

    def search(self, thread_id: str, query: str, k: int = 4) -> list[str]:
        result = self._collection.query(query_texts=[query], n_results=k, where={"thread_id": thread_id})
        docs = result.get("documents") or []
        return docs[0] if docs else []


def retrieval_tool(memory: "MemoryStore") -> ToolSpec:
    """Wraps semantic search as a ToolSpec — the 'RAG retrieval tools' chip under the
    Tools Gateway. Register it on a ToolRegistry for agents that retrieve explicitly,
    as an alternative (or complement) to orchestration.py's automatic context injection.

    `session_id` (not the model's job to supply — see memory_write_tool's
    own docstring for why) is auto-injected by orchestration.py's
    make_act_node whenever a tool declares a parameter with that exact
    name, overriding whatever the model passed or left out."""

    def search_memory(session_id: str, query: str) -> list[str]:
        return memory.semantic.search(session_id, query)

    return ToolSpec(
        name="search_memory",
        description="Search this thread's stored knowledge for passages relevant to a query.",
        parameters={"session_id": "string", "query": "string"},
        fn=search_memory,
    )


def memory_write_tool(memory: "MemoryStore") -> ToolSpec:
    """The write-side counterpart to retrieval_tool: lets the AGENT ITSELF
    decide something is worth remembering mid-conversation (the user just
    stated a fact that isn't in any uploaded document or prior context),
    rather than relying only on orchestration.py's automatic, read-only
    context injection. Register alongside retrieval_tool on a ToolRegistry.

    `session_id` is a declared parameter specifically so
    orchestration.py's make_act_node auto-injects the graph's REAL session
    id and overrides whatever the model supplied — a model should never be
    trusted to correctly name (or worse, be able to spoof) which session's
    memory it's writing into; a wrong or malicious value here would poison
    a DIFFERENT session's memory, not just misread one."""

    def save_memory(session_id: str, fact: str) -> dict:
        memory.semantic.upsert(session_id, fact, {"source": "agent_saved", "type": "agent_memory"})
        return {"saved": True, "fact": fact}

    return ToolSpec(
        name="save_memory",
        description=(
            "Save a specific fact worth remembering for the rest of this conversation — something the user "
            "just told you that isn't already in an uploaded document or earlier context. Use it for durable, "
            "specific facts (a stated preference, a detail they'll expect you to recall later), not for "
            "routinely restating information that's already retrievable."
        ),
        parameters={"session_id": "string", "fact": "string"},
        fn=save_memory,
    )


def profile_write_tool(memory: "MemoryStore") -> ToolSpec:
    """Lets the agent update a user's PROFILE — durable facts that outlive
    one session (a preference, a tier, a name), distinct from
    memory_write_tool's per-SESSION facts. Composes with AgentConfig.user_id
    (orchestration.py): set it on a graph and think() auto-loads/injects
    the profile into every turn's prompt; this tool is the write side.

    `user_id` is a declared parameter for the same reason session_id is on
    memory_write_tool — orchestration.py's make_act_node auto-injects
    AgentConfig.user_id's resolved value here, overriding whatever the
    model supplied, so a model can never update a DIFFERENT real user's
    profile no matter what it names."""

    def update_user_profile(user_id: str, field: str, value: str) -> dict:
        memory.update_profile(user_id, **{field: value})
        return {"updated": True, "field": field, "value": value}

    return ToolSpec(
        name="update_user_profile",
        description=(
            "Remember one durable fact about this user that should persist across FUTURE sessions too, not "
            "just this conversation (a stated preference, a tier, something true about them generally) — "
            "`field` is a short key (e.g. 'preferred_contact'), `value` is what to remember for it."
        ),
        parameters={"user_id": "string", "field": "string", "value": "string"},
        fn=update_user_profile,
    )


# ---- Procedural memory ---------------------------------------------------------------

@dataclass
class ProceduralMemory:
    """Learned tool-use patterns: which sequence of tool calls tends to complete a given
    task type. orchestration.py records one sequence per completed task; best_sequence()
    is what a prompt-optimization pass (reinforcement.py) would promote into the prompt."""

    patterns: dict[str, list[tuple[str, ...]]] = field(default_factory=dict)

    def record(self, task_type: str, tool_sequence: list[str]) -> None:
        if tool_sequence:
            self.patterns.setdefault(task_type, []).append(tuple(tool_sequence))

    def best_sequence(self, task_type: str) -> list[str] | None:
        seqs = self.patterns.get(task_type)
        if not seqs:
            return None
        return list(Counter(seqs).most_common(1)[0][0])


# ---- Knowledge graph -------------------------------------------------------------------

class KnowledgeGraphStore(Protocol):
    """Formalizes what MemoryStore.knowledge_graph needs to support, so a real
    graph database (Neo4j, a triple store, whatever) is a drop-in swap for
    KnowledgeGraph below — matches VectorStore's pattern for semantic memory."""
    def add(self, subject: str, relation: str, obj: str) -> None: ...
    def neighbors(self, entity: str) -> list[tuple[str, str]]: ...


@dataclass
class KnowledgeGraph:
    """In-memory (subject, relation, object) triple store for entity relationships a
    similarity search won't surface — e.g. 'order A100' -[placed_by]-> 'customer 42'
    — plus a lightweight ontology: named classes with a subclass hierarchy, and
    instance-of typing, so 'is a gold-tier customer entitled to expedited refunds'
    is answerable by walking a class hierarchy, not just by literal triples. Swap
    for Neo4j/RDFLib/etc. by matching the KnowledgeGraphStore protocol above."""

    triples: list[tuple[str, str, str]] = field(default_factory=list)
    _parents: dict[str, str] = field(default_factory=dict)          # class -> parent class
    _instances: dict[str, set[str]] = field(default_factory=dict)   # entity -> {classes}

    def add(self, subject: str, relation: str, obj: str) -> None:
        self.triples.append((subject, relation, obj))

    def neighbors(self, entity: str) -> list[tuple[str, str]]:
        out = [(r, o) for s, r, o in self.triples if s == entity]
        out += [(r, s) for s, r, o in self.triples if o == entity]
        return out

    def add_class(self, name: str, *, parent: str | None = None) -> None:
        if parent is not None:
            self._parents[name] = parent

    def instance_of(self, entity: str, class_name: str) -> None:
        self._instances.setdefault(entity, set()).add(class_name)

    def is_a(self, class_name: str, ancestor: str) -> bool:
        """True if class_name == ancestor or ancestor is anywhere up its parent chain."""
        seen, current = set(), class_name
        while current is not None:
            if current == ancestor:
                return True
            if current in seen:  # cycle guard
                return False
            seen.add(current)
            current = self._parents.get(current)
        return False

    def types_of(self, entity: str) -> set[str]:
        """Every class an entity belongs to, including inherited (super)classes."""
        result: set[str] = set()
        for cls in self._instances.get(entity, set()):
            result.add(cls)
            current = self._parents.get(cls)
            while current is not None and current not in result:
                result.add(current)
                current = self._parents.get(current)
        return result


# ---- The layer, assembled ---------------------------------------------------------------

@dataclass
class MemoryStore:
    working: dict[str, dict[str, Any]] = field(default_factory=dict)  # thread_id -> scratch state
    episodic: dict[str, list[dict]] = field(default_factory=dict)  # thread_id -> turns
    semantic: VectorStore = field(default_factory=InMemoryVectorStore)
    procedural: ProceduralMemory = field(default_factory=ProceduralMemory)
    knowledge_graph: KnowledgeGraph = field(default_factory=KnowledgeGraph)
    profiles: dict[str, dict[str, Any]] = field(default_factory=dict)  # user_id -> profile

    def append_turn(self, thread_id: str, turn: dict) -> None:
        self.episodic.setdefault(thread_id, []).append(turn)

    def history(self, thread_id: str) -> list[dict]:
        return self.episodic.get(thread_id, [])

    def get_profile(self, user_id: str) -> dict[str, Any]:
        return self.profiles.get(user_id, {})

    def update_profile(self, user_id: str, **fields: Any) -> None:
        self.profiles.setdefault(user_id, {}).update(fields)


# ---- Context Engine: retrieve -> rank -> filter -> compress -> assemble -> budget ----

@dataclass
class ContextEngine:
    """The full pipeline, not just retrieve+assemble. Each stage is a plain method
    you can override for a real reranker/tokenizer; the defaults are simple and
    real (keyword-overlap reranking, a redact() filter, char-budget compression),
    not stubs. Optional: orchestration.py falls back to memory.semantic.search()
    directly when no ContextEngine is supplied — this is an upgrade, not a
    dependency."""

    memory: MemoryStore
    max_tokens: int = 2000
    chars_per_token: float = 4.0  # rough, dependency-free estimate

    def retrieve(self, thread_id: str, query: str, *, k: int = 8) -> list[str]:
        return self.memory.semantic.search(thread_id, query, k=k)

    def rank(self, query: str, passages: list[str]) -> list[str]:
        """Keyword-overlap density reranking; override for a real cross-encoder."""
        terms = set(query.lower().split())

        def overlap(passage: str) -> float:
            words = passage.lower().split()
            return len(terms & set(words)) / max(len(words), 1)

        return sorted(passages, key=overlap, reverse=True)

    def filter(self, passages: list[str], *, redact_pii: bool = True, drop_injections: bool = True) -> list[str]:
        """Retrieved passages are untrusted content (an uploaded document, a
        tool result) — not the live user turn orchestration.py's check_input
        screens — so this is where PII redaction AND injection screening for
        RAG content both belong. drop_injections removes a passage entirely
        (rather than redacting it) if it trips the same marker check
        check_input uses, closing the indirect-prompt-injection gap where
        planted instructions in a document would otherwise reach the prompt
        completely unscreened."""
        from .guardrails import looks_like_injection, redact
        if drop_injections:
            passages = [p for p in passages if not looks_like_injection(p)]
        if redact_pii:
            passages = [redact(p) for p in passages]
        return passages

    def compress(self, passages: list[str], *, max_chars_per_passage: int = 500) -> list[str]:
        def clip(p: str) -> str:
            return p if len(p) <= max_chars_per_passage else p[:max_chars_per_passage].rsplit(" ", 1)[0] + "…"
        return [clip(p) for p in passages]

    def assemble(self, passages: list[str]) -> str:
        return "\n".join(f"- {p}" for p in passages)

    def budget(self, text: str) -> str:
        """Token budgeting via the char-per-token estimate — trims to fit max_tokens."""
        max_chars = int(self.max_tokens * self.chars_per_token)
        return text if len(text) <= max_chars else text[:max_chars].rsplit("\n", 1)[0]

    def build(self, thread_id: str, query: str, *, k: int = 8) -> str:
        passages = self.retrieve(thread_id, query, k=k)
        passages = self.rank(query, passages)
        passages = self.filter(passages)
        passages = self.compress(passages)
        return self.budget(self.assemble(passages))
