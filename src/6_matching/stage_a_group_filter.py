"""
Step 6a: Obligation Group Filter

比對方向:法規 norm → 合約 norm。
對每個 (法規 norm, 合約 norm) 配對,若雙方 obligation_groups 沒有任何交集,
直接剔除。obligation_groups 為空的合約 norm(被誤抽的 heading / boilerplate)
會因「空集合與任何 group 都無交集」自然被過濾掉。

輸入:Neo4j(Norm.obligation_groups)。
輸出:stage_a_pairs.json —— 通過交集過濾的候選配對,供 6b 使用。

使用方式:
    python stage_a_group_filter.py
    python stage_a_group_filter.py --regulatory-doc "GDPR_DPA_Requirements" --contract-doc Online124
"""

import json
import argparse
import yaml
import sys
from pathlib import Path
from typing import List, Dict
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ==================== 實驗參數 (Experimental Parameters) ====================
REGULATORY_DOC = "GDPR_DPA_Requirements"
CONTRACT_DOC = "Online124"
OUTPUT_DIR = "../../output/compliance_results"
CONFIG_PATH = "../../config.yaml"
# ==============================================================================


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["neo4j"]


def fetch_norms(driver, database: str, document_id: str) -> List[Dict]:
    """從 KG 撈某文件的所有 Norm 的 clause_id + obligation_groups + belongs_to。"""
    query = """
    MATCH (d:Document {document_id: $doc})-[:CONTAINS]->(n:Norm)
    RETURN n.clause_id AS clause_id,
           n.obligation_groups AS obligation_groups,
           n.belongs_to AS belongs_to
    """
    norms = []
    with driver.session(database=database) as session:
        for r in session.run(query, doc=document_id):
            norms.append({
                "clause_id": r["clause_id"],
                "obligation_groups": list(r["obligation_groups"] or []),
                "belongs_to": r["belongs_to"],
            })
    return norms


def main():
    parser = argparse.ArgumentParser(description="Step 6a: Obligation Group Filter")
    parser.add_argument("--config", type=str, default=CONFIG_PATH)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    parser.add_argument("--regulatory-doc", type=str, default=REGULATORY_DOC)
    parser.add_argument("--contract-doc", type=str, default=CONTRACT_DOC)
    args = parser.parse_args()

    print("=" * 70)
    print("Step 6a: Obligation Group Filter")
    print(f"法規: {args.regulatory_doc} | 合約: {args.contract_doc}")
    print("=" * 70)

    config = load_config(args.config)
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["username"], config["password"]))
        driver.verify_connectivity()
        print(f"Neo4j 連線成功")
    except (ServiceUnavailable, AuthError) as e:
        raise ConnectionError(f"Neo4j 連線失敗: {e}")

    database = config.get("database", "neo4j")
    try:
        reg_norms = fetch_norms(driver, database, args.regulatory_doc)
        con_norms = fetch_norms(driver, database, args.contract_doc)
        print(f"   法規 norms: {len(reg_norms)} | 合約 norms: {len(con_norms)}")
        if not reg_norms or not con_norms:
            raise RuntimeError("法規或合約 norms 為空,請先執行 Step 5 kg_writer.py 寫入兩份文件")

        pairs = []
        n_total = len(reg_norms) * len(con_norms)
        con_empty = sum(1 for c in con_norms if not c["obligation_groups"])

        for reg in reg_norms:
            reg_groups = set(reg["obligation_groups"])
            for con in con_norms:
                shared = reg_groups & set(con["obligation_groups"])
                if shared:
                    pairs.append({
                        "reg_clause_id": reg["clause_id"],
                        "contract_clause_id": con["clause_id"],
                        "reg_belongs_to": reg["belongs_to"],
                        "shared_groups": sorted(shared),
                    })

        print(f"\n過濾結果:")
        print(f"   原始配對: {n_total}")
        print(f"   通過交集: {len(pairs)} ({len(pairs)/n_total*100:.1f}%)")
        print(f"   合約空 groups 條數(自然被排除): {con_empty}")

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "stage_a_pairs.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({
                "regulatory_doc": args.regulatory_doc,
                "contract_doc": args.contract_doc,
                "n_reg_norms": len(reg_norms),
                "n_contract_norms": len(con_norms),
                "n_pairs": len(pairs),
                "pairs": pairs,
            }, f, indent=2, ensure_ascii=False)
        print(f"\n結果已儲存: {output_path}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
