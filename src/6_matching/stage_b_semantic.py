"""
Step 6b: Semantic Filter (core_sentence embedding cosine)

對通過 6a 的配對,計算雙方 core_sentence embedding 的 cosine similarity,
過 threshold 的配對進入 6c。這一層是粗篩,目標高 recall。

OpenAI embedding 已是正規化單位向量,cosine 直接用點積即可(不需再正規化)。

輸入:stage_a_pairs.json + Neo4j(Norm.embedding, Norm.core_sentence)。
輸出:stage_b_pairs.json。

使用方式:
    python stage_b_semantic.py
    python stage_b_semantic.py --threshold 0.55
"""

import json
import argparse
import yaml
import sys
import numpy as np
from pathlib import Path
from typing import Dict, List
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ==================== 實驗參數 (Experimental Parameters) ====================
# 粗篩高 recall:寧可多放一些進 6c,不要在這裡漏掉。可調。
SIMILARITY_THRESHOLD = 0.50

INPUT_PATH = "../../output/compliance_results/stage_a_pairs.json"
OUTPUT_DIR = "../../output/compliance_results"
CONFIG_PATH = "../../config.yaml"
# ==============================================================================


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["neo4j"]


def fetch_embeddings(driver, database: str, clause_ids: List[str]) -> Dict[str, Dict]:
    """撈指定 clause_id 的 core_sentence + embedding。"""
    query = """
    MATCH (n:Norm)
    WHERE n.clause_id IN $ids
    RETURN n.clause_id AS clause_id,
           n.core_sentence AS core_sentence,
           n.embedding AS embedding
    """
    out = {}
    with driver.session(database=database) as session:
        for r in session.run(query, ids=clause_ids):
            out[r["clause_id"]] = {
                "core_sentence": r["core_sentence"],
                "embedding": np.array(r["embedding"]) if r["embedding"] else None,
            }
    return out


def main():
    parser = argparse.ArgumentParser(description="Step 6b: Semantic Filter (cosine)")
    parser.add_argument("--input", type=str, default=INPUT_PATH)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    parser.add_argument("--config", type=str, default=CONFIG_PATH)
    parser.add_argument("--threshold", type=float, default=SIMILARITY_THRESHOLD)
    args = parser.parse_args()

    print("=" * 70)
    print("Step 6b: Semantic Filter (core_sentence embedding cosine)")
    print(f"閾值: {args.threshold}  (粗篩,高 recall)")
    print("=" * 70)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"找不到 6a 輸出: {input_path}。請先執行 stage_a_group_filter.py")
    with open(input_path, "r", encoding="utf-8") as f:
        stage_a = json.load(f)
    pairs = stage_a["pairs"]
    print(f"讀取 6a 候選配對: {len(pairs)}")

    config = load_config(args.config)
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["username"], config["password"]))
        driver.verify_connectivity()
        print("Neo4j 連線成功")
    except (ServiceUnavailable, AuthError) as e:
        raise ConnectionError(f"Neo4j 連線失敗: {e}")

    database = config.get("database", "neo4j")
    try:
        all_ids = sorted({p["reg_clause_id"] for p in pairs} | {p["contract_clause_id"] for p in pairs})
        emb = fetch_embeddings(driver, database, all_ids)

        missing = [cid for cid in all_ids if cid not in emb or emb[cid]["embedding"] is None]
        if missing:
            print(f" {len(missing)} 個 norm 缺 embedding,相關配對將跳過: {missing[:8]}")

        survivors = []
        for p in pairs:
            r_id, c_id = p["reg_clause_id"], p["contract_clause_id"]
            r, c = emb.get(r_id), emb.get(c_id)
            if not r or not c or r["embedding"] is None or c["embedding"] is None:
                continue
            sim = float(np.dot(r["embedding"], c["embedding"]))
            if sim >= args.threshold:
                survivors.append({
                    **p,
                    "similarity": sim,
                    "reg_core_sentence": r["core_sentence"],
                    "contract_core_sentence": c["core_sentence"],
                })

        survivors.sort(key=lambda x: x["similarity"], reverse=True)

        print(f"\n6b 結果:")
        print(f"   輸入配對: {len(pairs)}")
        print(f"   通過閾值 (>= {args.threshold}): {len(survivors)}")
        if survivors:
            scores = [s["similarity"] for s in survivors]
            print(f"   相似度 最高 {max(scores):.4f} / 最低 {min(scores):.4f} / 平均 {np.mean(scores):.4f}")
            print("\nTop 5:")
            for s in survivors[:5]:
                print(f"   {s['reg_clause_id']} ↔ {s['contract_clause_id']}: {s['similarity']:.4f}")

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "stage_b_pairs.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "threshold": args.threshold,
                "n_input": len(pairs),
                "n_survivors": len(survivors),
                "pairs": survivors,
            }, f, indent=2, ensure_ascii=False)
        print(f"\n結果已儲存: {output_path}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
