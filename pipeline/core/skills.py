from __future__ import annotations

import os

import chromadb
from langchain_core.embeddings import Embeddings

from models.skill import Skill, SkillMatch

_COLLECTION_NAME = "skills"


class SkillStore:
    """
    ChromaDB-backed semantic skill store.
    Accepts an injected Embeddings instance — never instantiates a provider itself.
    data_path is the directory where ChromaDB persists its files.
    """

    def __init__(self, embeddings: Embeddings, data_path: str | None = None) -> None:
        self._embeddings = embeddings
        self._data_path = data_path or os.environ.get("CHROMA_DATA_PATH", "./chroma_data")
        self._client = chromadb.PersistentClient(path=self._data_path)
        self._collection = self._client.get_or_create_collection(
            name=_COLLECTION_NAME,
            metadata={"hnsw:space": "cosine"},
        )

    def upsert(self, skill: Skill) -> None:
        """Add or update a skill in the collection. Idempotent by skill_id."""
        self._collection.upsert(
            ids=[skill.skill_id],
            embeddings=[skill.embedding],
            documents=[skill.body],
            metadatas=[{
                "name": skill.name,
                "description": skill.description,
                "category": skill.category,
                "tags": ",".join(skill.tags),
                "source_path": skill.source_path,
                "supports_codex": str(skill.supports_codex),
                "supports_claude": str(skill.supports_claude),
            }],
        )

    def upsert_batch(self, skills: list[Skill]) -> None:
        """Batch upsert for bulk ingestion. More efficient than calling upsert() per skill."""
        if not skills:
            return
        self._collection.upsert(
            ids=[s.skill_id for s in skills],
            embeddings=[s.embedding for s in skills],
            documents=[s.body for s in skills],
            metadatas=[{
                "name": s.name,
                "description": s.description,
                "category": s.category,
                "tags": ",".join(s.tags),
                "source_path": s.source_path,
                "supports_codex": str(s.supports_codex),
                "supports_claude": str(s.supports_claude),
            } for s in skills],
        )

    def query(self, text: str, n_results: int = 10) -> list[SkillMatch]:
        """
        Semantic search against the skill collection.
        Returns SkillMatch list sorted by score descending (higher = more relevant).
        Cosine distance is converted to similarity: score = 1.0 - distance.
        """
        query_embedding = self._embeddings.embed_query(text)
        results = self._collection.query(
            query_embeddings=[query_embedding],
            n_results=min(n_results, self._collection.count() or 1),
            include=["documents", "metadatas", "distances"],
        )

        matches: list[SkillMatch] = []
        ids = results["ids"][0]
        docs = results["documents"][0]
        metas = results["metadatas"][0]
        distances = results["distances"][0]

        for skill_id, body, meta, distance in zip(ids, docs, metas, distances):
            tags_raw = meta.get("tags", "")
            tags = [t for t in tags_raw.split(",") if t] if tags_raw else []

            skill = Skill(
                skill_id=skill_id,
                name=meta.get("name", ""),
                description=meta.get("description", ""),
                category=meta.get("category", ""),
                tags=tags,
                body=body,
                embedding=[],
                source_path=meta.get("source_path", ""),
                supports_codex=meta.get("supports_codex", "True") == "True",
                supports_claude=meta.get("supports_claude", "True") == "True",
            )
            matches.append(SkillMatch(skill=skill, score=1.0 - distance))

        return sorted(matches, key=lambda m: m.score, reverse=True)

    def count(self) -> int:
        return self._collection.count()

    def embed_and_upsert(self, skill_dicts: list[dict]) -> int:
        """
        Embed a list of raw skill dicts (from ingest_skills.load_all_skills)
        and upsert into the collection. Returns count of skills ingested.
        Processes in batches of 100 to avoid OOM on embedding calls.
        """
        batch_size = 100
        total = 0

        for i in range(0, len(skill_dicts), batch_size):
            batch = skill_dicts[i : i + batch_size]
            texts = [
                d["description"] + "\n" + d["body"][:500] for d in batch
            ]
            embeddings = self._embeddings.embed_documents(texts)

            skills = [
                Skill(
                    skill_id=d["skill_id"],
                    name=d["name"],
                    description=d["description"],
                    category=d.get("category", ""),
                    tags=d.get("tags", []),
                    body=d["body"],
                    embedding=emb,
                    source_path=d["source_path"],
                    supports_codex=d.get("supports_codex", True),
                    supports_claude=d.get("supports_claude", True),
                )
                for d, emb in zip(batch, embeddings)
            ]
            self.upsert_batch(skills)
            total += len(skills)

        return total
