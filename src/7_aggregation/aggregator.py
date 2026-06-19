"""
Step 7: Aggregation (子 norm 結果聚合 + and/or 開關)

把 6c 的每對判定收斂回「法規要求」層級(rule_id = belongs_to 非空則用之,否則
用 clause_id),產生最終合規結果。父 norm 與子 norm 的 Compliant 判定都算進同一個
R 層級。

and/or 開關 (AGGREGATION_MODE):
  - "OFF"(預設,對應目前 ground truth 標記方式):某法規要求只要有任一 norm
    (父或子)被判 Compliant,該要求即視為被召回。compliant.csv 輸出所有 Compliant 配對。
  - "ON"(較嚴格):依父要求的 logic_type 判斷。AND 需該要求所有子 norm 都有
    Compliant 配對才算「完全合規」;OR 滿足其一即可。ON 模式下 compliant.csv 只輸出
    隸屬「完全合規」要求的 Compliant 配對。

輸出:
  - compliant.csv  —— 欄位:rule_id, contract_clause_id, retrieved_sentence, retrieval_score
                      (retrieved_sentence = 合約逐字 source_text;retrieval_score = 6b cosine)
  - requirement_summary.json —— 每個 rule_id 的聚合狀態(供檢視)

使用方式:
    python aggregator.py
    python aggregator.py --mode ON
"""

import json
import csv
import argparse
import sys
from pathlib import Path
from typing import Dict, List
from collections import defaultdict

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ==================== 實驗參數 (Experimental Parameters) ====================
AGGREGATION_MODE = "OFF"     # "OFF"(預設) 或 "ON"

# INPUT_PATH = "../../output/compliance_results/stage_c_results.json"
# OUTPUT_DIR = "../../output/compliance_results"
# ---
INPUT_PATH = "../../output/compliance_results/stage_c_results.json"
OUTPUT_DIR = "../../output/compliance_results"
# ---
# ON 模式需要法規 norms 以取得 logic_type 與子 norm 清單(OFF 模式不需要)
REG_NORMS_PATH = "../../output/norms/GDPR_DPA_Requirements_norms.json"
COMPLIANT_VERDICT = "Compliant"
# ==============================================================================


def _origin(r: Dict) -> str:
    """合約端去重單位:原始 clause(去掉 _a/_b 多義務拆分後綴)。"""
    return r.get("contract_origin_clause") or r.get("contract_clause_id")


def load_reg_structure(reg_norms_path: str):
    """
    回傳:
      logic_by_rule:    {rule_id: "AND"/"OR"/None}
      children_by_rule: {rule_id: set(child_clause_id)}
    """
    path = Path(reg_norms_path)
    logic_by_rule: Dict[str, str] = {}
    children_by_rule: Dict[str, set] = defaultdict(set)
    if not path.exists():
        print(f" 找不到法規 norms: {path}(ON 模式需要;OFF 模式可忽略)")
        return logic_by_rule, children_by_rule

    with open(path, "r", encoding="utf-8") as f:
        norms = json.load(f)
    for n in norms:
        if n.get("logic_type"):
            logic_by_rule[n["clause_id"]] = n["logic_type"]
        if n.get("belongs_to"):
            children_by_rule[n["belongs_to"]].add(n["clause_id"])
    return logic_by_rule, children_by_rule


def main():
    parser = argparse.ArgumentParser(description="Step 7: Aggregation (and/or switch)")
    parser.add_argument("--input", type=str, default=INPUT_PATH)
    parser.add_argument("--reg-norms", type=str, default=REG_NORMS_PATH)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    parser.add_argument("--mode", type=str, default=AGGREGATION_MODE, choices=["OFF", "ON"])
    args = parser.parse_args()

    print("=" * 70)
    print(f"Step 7: Aggregation (mode={args.mode})")
    print("=" * 70)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"找不到 6c 輸出: {input_path}。請先執行 stage_c_reasoning.py")
    with open(input_path, "r", encoding="utf-8") as f:
        stage_c = json.load(f)
    results = stage_c["results"]
    print(f"讀取 6c 判定: {len(results)}")

    # 只保留 Compliant 配對,依 rule_id 分組
    compliant = [r for r in results if r.get("verdict") == COMPLIANT_VERDICT]
    by_rule: Dict[str, List[Dict]] = defaultdict(list)
    for r in compliant:
        by_rule[r["rule_id"]].append(r)
    # 被判 Compliant 的子 norm clause_id(供 ON 模式判斷)
    satisfied_norms_by_rule: Dict[str, set] = defaultdict(set)
    for r in compliant:
        satisfied_norms_by_rule[r["rule_id"]].add(r["reg_clause_id"])

    logic_by_rule, children_by_rule = ({}, {})
    if args.mode == "ON":
        logic_by_rule, children_by_rule = load_reg_structure(args.reg_norms)

    # 計算每個 rule 的聚合狀態
    summary = {}
    emit_rules = set()  # 哪些 rule 的配對要寫進 compliant.csv
    for rule_id, pairs in by_rule.items():
        status = "recalled"
        if args.mode == "ON":
            children = children_by_rule.get(rule_id, set())
            logic = logic_by_rule.get(rule_id)
            satisfied = satisfied_norms_by_rule.get(rule_id, set())
            if not children:
                # 非 enumerated:有 Compliant 即完全合規
                status = "fully_compliant"
            elif logic == "AND":
                status = "fully_compliant" if children <= satisfied else "partially_compliant"
            else:  # OR(或 logic 缺失時寬鬆視為 OR)
                status = "fully_compliant" if (satisfied & children or rule_id in satisfied) else "partially_compliant"
            if status == "fully_compliant":
                emit_rules.add(rule_id)
        else:
            emit_rules.add(rule_id)  # OFF:全部召回

        summary[rule_id] = {
            "status": status,
            "n_compliant_pairs": len(pairs),
            "satisfied_norms": sorted(satisfied_norms_by_rule.get(rule_id, set())),
            # 去重單位:原始 clause(origin_clause)
            "contract_matches": sorted({_origin(p) for p in pairs}),
        }

    # 產出 compliant.csv:以 (rule_id, origin_clause) 去重,同一原始 clause 多個子 norm
    # 配到同一法規只算一次(取最高相似度的那筆;source_text 父子相同)。
    best: Dict[tuple, Dict] = {}
    for r in compliant:
        if r["rule_id"] not in emit_rules:
            continue
        origin = _origin(r)
        key = (r["rule_id"], origin)
        sim = r.get("similarity")
        cur = best.get(key)
        if cur is None or (sim is not None and (cur["sim"] is None or sim > cur["sim"])):
            best[key] = {
                "rule_id": r["rule_id"],
                "origin": origin,
                "source_text": r.get("contract_source_text") or "",
                "sim": sim,
            }
    rows = []
    for key in sorted(best.keys()):
        b = best[key]
        rows.append([
            b["rule_id"],
            b["origin"],
            b["source_text"],
            f"{b['sim']:.4f}" if b["sim"] is not None else "",
        ])

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    compliant_csv = output_dir / "compliant.csv"
    with open(compliant_csv, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["rule_id", "contract_clause_id", "retrieved_sentence", "retrieval_score"])
        writer.writerows(rows)

    summary_path = output_dir / "requirement_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump({
            "mode": args.mode,
            "n_rules_recalled": len(by_rule),
            "n_rules_emitted": len(emit_rules),
            "summary": summary,
        }, f, indent=2, ensure_ascii=False)

    print(f"\nmode={args.mode}: compliant.csv {len(rows)} 列")
    print(f"compliant.csv 已儲存: {compliant_csv}")
    print(f"requirement_summary.json 已儲存: {summary_path}")


if __name__ == "__main__":
    main()
