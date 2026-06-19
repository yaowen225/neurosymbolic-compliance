# 共用判準段已對齊現行 main 6c —— 直接取用同資料夾 ablation_prompts.py(單一對齊來源,
# main 之後更新時這裡自動跟著對齊;本變體只保留自己的輸入差異)。
import importlib.util as _u
from pathlib import Path as _P
_apspec = _u.spec_from_file_location('ablation_prompts', _P(__file__).resolve().parent / 'ablation_prompts.py')
_ap = _u.module_from_spec(_apspec); _apspec.loader.exec_module(_ap)
REASONING_PROMPT = _ap.TEXT_ONLY_PROMPT

# ============================================================================
# 6c 變體:TEXT-ONLY(只看原文,完全不給結構化欄位)—— 證明結構化貢獻的對照組。
# 程式邏輯參考純結構化版 stage_c_reasoning.py,差別:
#   - judge 只餵雙方 source_text(原文),不餵任何結構化欄位。
#   - parent_context 只給父條款的「原文」。
#   - 輸出到自己的 OUTPUT_DIR(variant_textonly)。
# prompt(上面 REASONING_PROMPT)由使用者維護,程式不改。
# ============================================================================

import os
import json
import argparse
import yaml
import sys
from pathlib import Path
from typing import Dict, List
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost_meter


# ==================== 實驗參數 ====================
LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
MAX_RETRIES = 3

INPUT_PATH = "../output/compliance_results/stage_b_pairs.json"
OUTPUT_DIR = "../output/compliance_results/variant_text_only"
FAILURE_DIR = "../output/failures"
CONFIG_PATH = "../config.yaml"
REGULATORY_DOC = "GDPR_DPA_Requirements"
CONTRACT_DOC = "Online124"
COST_SYSTEM = "variant_text_only"
# ====================================================

API_TIMEOUT = 60
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["neo4j"]


def fetch_structured(driver, database: str, document_id: str) -> Dict[str, Dict]:
    """
    與純結構化版同一查詢(取回 source_text + parent/belongs_to 供父脈絡與去重)。
    text-only 版本只用其中的 source_text;結構化欄位不餵進 prompt。
    """
    query = """
    MATCH (d:Document {document_id: $doc})-[:CONTAINS]->(n:Norm)
    OPTIONAL MATCH (n)-[:HAS_ACTOR]->(a)
    OPTIONAL MATCH (n)-[:HAS_ACTION]->(ac)
    OPTIONAL MATCH (n)-[:HAS_OBJECT]->(o)
    OPTIONAL MATCH (n)-[:HAS_RECIPIENT]->(rec)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_CONDITION]->(cond)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_TIMING]->(tim)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_MANNER]->(man)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_TARGET]->(tar)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_LOCATION]->(loc)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_CAUSE]->(cau)
    RETURN n.clause_id AS clause_id, n.modality AS modality,
           n.belongs_to AS belongs_to, n.parent AS parent,
           n.origin_clause AS origin_clause, n.source_text AS source_text,
           a.value AS actor, ac.value AS action, o.value AS object, rec.value AS recipient,
           cond.value AS condition, tim.value AS timing, man.value AS manner,
           tar.value AS target, loc.value AS location, cau.value AS cause
    """
    out = {}
    with driver.session(database=database) as session:
        for r in session.run(query, doc=document_id):
            out[r["clause_id"]] = {k: r[k] for k in r.keys()}
    return out


def _fmt(v) -> str:
    return "null" if v is None or (isinstance(v, str) and not v.strip()) else str(v)


def build_parent_context(con_parent: Dict) -> str:
    """TEXT-ONLY 父脈絡:只給父條款的原文。"""
    if not con_parent:
        return ""
    return (
        "\n## Parent clause context (original text)\n"
        "The contract obligation above is a SUB-ITEM of a larger parent clause. Treat the "
        "sub-item TOGETHER WITH this parent context as the contract's fulfillment of the "
        "requirement (the parent-level duty also counts as performed by the contract).\n"
        f"Original text: {_fmt(con_parent.get('source_text'))}"
    )


class Reasoner:
    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)
        self.failures = []

    @retry(stop=stop_after_attempt(MAX_RETRIES),
           wait=wait_exponential(multiplier=RETRY_WAIT_EXPONENTIAL_MULTIPLIER, max=RETRY_WAIT_EXPONENTIAL_MAX))
    def judge(self, reg: Dict, con: Dict, con_parent: Dict = None) -> Dict:
        prompt = REASONING_PROMPT
        prompt = prompt.replace("{reg_source_text}", _fmt(reg.get("source_text")))
        prompt = prompt.replace("{contract_source_text}", _fmt(con.get("source_text")))
        prompt = prompt.replace("{parent_context}", build_parent_context(con_parent))

        response = self.client.chat.completions.create(
            model=LLM_MODEL,
            reasoning_effort=REASONING_EFFORT,
            messages=[
                {"role": "system", "content": "You are a compliance entailment reviewer."},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )
        cost_meter.add_chat(response)
        return json.loads(response.choices[0].message.content)


def main():
    parser = argparse.ArgumentParser(description="Step 6c variant: TEXT-ONLY (original text only)")
    parser.add_argument("--input", type=str, default=INPUT_PATH)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    parser.add_argument("--failure-dir", type=str, default=FAILURE_DIR)
    parser.add_argument("--config", type=str, default=CONFIG_PATH)
    parser.add_argument("--regulatory-doc", type=str, default=REGULATORY_DOC)
    parser.add_argument("--contract-doc", type=str, default=CONTRACT_DOC)
    args = parser.parse_args()

    print("=" * 70)
    print("Step 6c variant: TEXT-ONLY (只看原文,無結構化欄位)")
    print("=" * 70)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"找不到 6b 輸出: {input_path}。請先執行 stage_b_semantic.py")
    with open(input_path, "r", encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]
    print(f"讀取 6b 配對: {len(pairs)}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY")

    config = load_config(args.config)
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["username"], config["password"]))
        driver.verify_connectivity()
        print("Neo4j 連線成功")
    except (ServiceUnavailable, AuthError) as e:
        raise ConnectionError(f"Neo4j 連線失敗: {e}")

    database = config.get("database", "neo4j")
    cost_meter.configure(system=COST_SYSTEM, step="step6c_reasoning")
    reasoner = Reasoner(api_key)
    results = []

    try:
        reg_fields = fetch_structured(driver, database, args.regulatory_doc)
        con_fields = fetch_structured(driver, database, args.contract_doc)
        print(f"   法規 norms: {len(reg_fields)} | 合約: {len(con_fields)}")

        total = len(pairs)
        for i, p in enumerate(pairs, 1):
            r_id, c_id = p["reg_clause_id"], p["contract_clause_id"]
            reg = reg_fields.get(r_id)
            con = con_fields.get(c_id)
            if not reg or not con:
                print(f" 缺欄位,跳過: {r_id} <-> {c_id}")
                continue
            if i % 10 == 0 or i == total:
                print(f"   進度 {i}/{total}")

            parent_id = con.get("belongs_to") or con.get("parent")
            con_parent = con_fields.get(parent_id) if parent_id else None

            try:
                verdict = reasoner.judge(reg, con, con_parent)
            except Exception as e:
                reasoner.failures.append({"reg_clause_id": r_id, "contract_clause_id": c_id, "error": str(e)})
                print(f"判斷失敗: {r_id} <-> {c_id} - {e}")
                continue

            results.append({
                "reg_clause_id": r_id,
                "contract_clause_id": c_id,
                "reg_belongs_to": reg.get("belongs_to"),
                "rule_id": reg.get("belongs_to") or r_id,
                "contract_origin_clause": con.get("origin_clause") or c_id,
                "parent_context_used": bool(con_parent),
                "similarity": p.get("similarity"),
                "shared_groups": p.get("shared_groups", []),
                "verdict": verdict.get("verdict"),
                "core_alignment_check": verdict.get("core_alignment_check"),
                "condition_check": verdict.get("condition_check"),
                "modality_check": verdict.get("modality_check"),
                "other_constraints_check": verdict.get("other_constraints_check"),
                "contract_source_text": con.get("source_text"),
            })

        cost_meter.flush()

        from collections import Counter
        vc = Counter(r["verdict"] for r in results)
        print(f"\n6c(text-only) 判定分佈: {dict(vc)}")

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "stage_c_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"n_input": len(pairs), "n_judged": len(results),
                       "verdict_counts": dict(vc), "results": results},
                      f, indent=2, ensure_ascii=False)
        print(f"\n結果已儲存: {output_path}")

        if reasoner.failures:
            failure_dir = Path(args.failure_dir)
            failure_dir.mkdir(parents=True, exist_ok=True)
            with open(failure_dir / "stage_c_textonly_failures.json", "w", encoding="utf-8") as f:
                json.dump(reasoner.failures, f, indent=2, ensure_ascii=False)
    finally:
        driver.close()


if __name__ == "__main__":
    main()