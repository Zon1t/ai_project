"""Chroma 색인 만들기와 검색 (PRD F4-1, F4-2).

임베딩은 bge-m3를 직접 계산해 넣는다 (Chroma 기본 임베딩 모델을 내려받지 않음). 거리는 코사인.
- history: 학습 분할 결함 이력. 티켓 문장을 임베딩한다.
- rules: 사내 규칙 40건. 규칙 1건 = 청크 1개. 검색 때 코드 패턴(keywords) 일치에 가산점을 준다.

실행: python -m sr_agent.rag.index  (PYTHONPATH=src). 다시 실행하면 두 컬렉션을 새로 만든다.
"""

import argparse
import hashlib
import json
import re
from pathlib import Path

import chromadb
from chromadb.config import Settings

from sr_agent import config
from sr_agent.datagen.tickets import load_jsonl
from sr_agent.ml.corpus import load_tickets
from sr_agent.ml.features import Embedder, get_embedder
from sr_agent.rag.kb import history_docs, leaked_history, load_rules, rule_text

_HISTORY_META = ("history_id", "mutant_id", "ticket_ids", "ticket", "method", "root_cause", "fix_summary",
                 "inject_type", "operator")


def open_client(chroma_dir: Path | None = None) -> chromadb.ClientAPI:
    return chromadb.PersistentClient(path=str(chroma_dir or config.CHROMA_DIR),
                                     settings=Settings(anonymized_telemetry=False))


def _recreate(client: chromadb.ClientAPI, name: str):
    if name in [c.name for c in client.list_collections()]:
        client.delete_collection(name)
    return client.create_collection(name, embedding_function=None, metadata={"hnsw:space": "cosine"})


def _digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def build_index(
    tickets_csv: Path | None = None,
    mutants_path: Path | None = None,
    rules_path: Path | None = None,
    chroma_dir: Path | None = None,
    embedder: Embedder | None = None,
) -> dict:
    """이력·규칙 컬렉션을 새로 만든다. 학습 외 결함이 이력에 들어가면 만들지 않는다."""
    tickets_csv = Path(tickets_csv or config.TICKETS_CSV_PATH)
    mutants_path = Path(mutants_path or config.MUTANTS_PATH)
    rules_path = Path(rules_path or config.RULES_PATH)
    chroma_dir = Path(chroma_dir or config.CHROMA_DIR)
    embedder = embedder or get_embedder()

    tickets = load_tickets(tickets_csv)
    docs = history_docs(tickets, {m["mutant_id"]: m for m in load_jsonl(mutants_path)})
    if leaked := leaked_history(docs, tickets):
        return {"error": "학습 외 결함이 이력에 들어감", "mutant_ids": leaked[:20]}
    rules = load_rules(rules_path)

    client = open_client(chroma_dir)
    history = _recreate(client, config.HISTORY_COLLECTION)
    for start in range(0, len(docs), 500):
        batch = docs[start:start + 500]
        history.add(
            ids=[d["history_id"] for d in batch],
            embeddings=embedder.encode([d["ticket"] for d in batch]).tolist(),
            documents=[d["ticket"] for d in batch],
            metadatas=[{k: d[k] for k in _HISTORY_META} for d in batch],
        )
    rules_col = _recreate(client, config.RULES_COLLECTION)
    rules_col.add(
        ids=[r["rule_id"] for r in rules],
        embeddings=embedder.encode([rule_text(r) for r in rules]).tolist(),
        documents=[rule_text(r) for r in rules],
        metadatas=[{**{k: r[k] for k in ("rule_id", "category", "title", "description", "good_example",
                                         "severity", "check")},
                    "keywords": json.dumps(r["keywords"], ensure_ascii=False)} for r in rules],
    )
    manifest = {
        "embedding_model": embedder.key,
        "history": history.count(),
        "rules": rules_col.count(),
        "tickets_sha256": _digest(tickets_csv),
        "mutants_sha256": _digest(mutants_path),
        "rules_sha256": _digest(rules_path),
    }
    (chroma_dir / Path(config.RAG_MANIFEST_PATH).name).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    return manifest


def keyword_hits(rule_keywords: list[str], code: str) -> bool:
    return any(re.search(k, code, re.MULTILINE) for k in rule_keywords)


class KnowledgeBase:
    """만들어 둔 Chroma 색인을 열어 검색한다."""

    def __init__(self, chroma_dir: Path | None = None, embedder: Embedder | None = None):
        client = open_client(chroma_dir)
        names = [c.name for c in client.list_collections()]
        if not {config.HISTORY_COLLECTION, config.RULES_COLLECTION} <= set(names):
            raise FileNotFoundError("RAG 색인 없음 (python -m sr_agent.rag.index로 생성)")
        self.history = client.get_collection(config.HISTORY_COLLECTION)
        self.rules = client.get_collection(config.RULES_COLLECTION)
        self.embedder = embedder or get_embedder()

    def _embed(self, text: str) -> list[float]:
        return self.embedder.encode([text], save=False)[0].tolist()

    def search_history(self, query: str, top_k: int = 3) -> list[dict]:
        res = self.history.query(query_embeddings=[self._embed(query)], n_results=top_k)
        out = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0], strict=True):
            ticket = meta["ticket"]
            if len(ticket) > config.HISTORY_TEXT_CHARS:
                ticket = ticket[: config.HISTORY_TEXT_CHARS] + "…"
            out.append({"history_id": meta["history_id"], "ticket": ticket, "method": meta["method"],
                        "root_cause": meta["root_cause"], "fix_summary": meta["fix_summary"],
                        "score": round(1 - float(dist), 4)})
        return out

    def search_rules(self, code: str, top_k: int = 5) -> list[dict]:
        """임베딩 유사도 + 코드 패턴 가산점으로 규칙 전체를 다시 정렬한다 (40건이라 전부 본다)."""
        res = self.rules.query(query_embeddings=[self._embed(code)], n_results=self.rules.count())
        scored = []
        for meta, dist in zip(res["metadatas"][0], res["distances"][0], strict=True):
            hit = keyword_hits(json.loads(meta["keywords"]), code)
            score = 1 - float(dist) + (config.RULE_KEYWORD_BONUS if hit else 0.0)
            scored.append((score, meta["rule_id"], hit, meta))
        scored.sort(key=lambda x: (-x[0], x[1]))
        return [{"rule_id": rid, "title": meta["title"], "description": meta["description"],
                 "good_example": meta["good_example"], "severity": meta["severity"],
                 "matched_pattern": hit, "score": round(score, 4)}
                for score, rid, hit, meta in scored[:top_k]]


def main() -> None:
    parser = argparse.ArgumentParser(description="RAG 색인 생성 (PRD F4-1, F4-2)")
    parser.parse_args()
    print(json.dumps(build_index(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
