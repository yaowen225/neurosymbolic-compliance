"""
Step 2: Regulatory Obligation Group Classifier

讓 LLM 一次看完所有法規 clauses，自主發現主題類別 (obligation groups),
為每條法規分配 1+ 類別。輸出供 Step 3 (extraction) 與 Step 6a (group filter) 使用。

沿用 src_old 邏輯:LLM 全局 MECE 分類 + 驗證警告但不修正。

設計原則:
- 完全由 LLM 自主決定類別數量、名稱、定義 (no preset)
- 類別之間必須 MECE (Mutually Exclusive, Collectively Exhaustive)
- 但單條法規仍可同時屬於多個類別 (multi-label)
- 用 LLM 產出的完整名稱當識別字串 (不縮寫不編號)

依賴提醒:
- 若重跑本腳本重新產生 taxonomy,必須重跑 Step 3 的合約抽取
  (extract_contract.py),否則合約 obligation_groups 會對不上新 taxonomy。

使用方式:
    # 開發/測試: 直接修改下方參數後執行
    python regulatory_classifier.py

    # 生產/批次: 使用命令列參數
    python regulatory_classifier.py --input path/to/parsed.json --output-dir path/to/output/
"""

import os
import json
import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import List, Dict
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

# 設置 UTF-8 輸出編碼 (Windows 相容性)
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()

# 成本計算 + 生成 LLM 後端切換(預設不設 env 時等同原本)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import cost_meter
import gen_runtime


# ==================== 實驗參數 (Experimental Parameters) ====================
# 開發測試時直接修改這裡的參數

INPUT_PATH = "../../output/parsed_regulatory/GDPR_DPA_Requirements_parsed.json"
OUTPUT_DIR = "../../output/obligation_groups"

LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
MAX_RETRIES = 3

# ==============================================================================


# ==================== 預設參數 ====================
API_TIMEOUT = 120
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10
# =====================================================


CLASSIFICATION_PROMPT = """\
You are designing a taxonomy of obligation categories for a regulatory document on data protection / compliance.

## Your Task

You will see the FULL text of every clause in the document. Do BOTH of the following in one response:

(A) Discover the natural set of obligation categories that emerge from the content.
(B) Assign each clause to one or more of those categories.

## Rules for the Category Taxonomy

The category SET itself must satisfy MECE:
- **Mutually Exclusive**: each category's definition must describe a DIFFERENT theme. The definitions should not overlap — if two categories cover the same subject matter, merge them.
- **Collectively Exhaustive**: every clause in the document must fit at least one category. No clause should fall outside the taxonomy.

Important — MECE applies to the CATEGORY DEFINITIONS, not to clause assignments:
- A single clause MAY still belong to multiple categories. Multi-label is expected, because real obligations often touch several themes (e.g. an obligation about "notify the controller of a breach in writing within 72 hours" might belong to both a "Breach Notification" category and a "Communication Form & Timing" category).
- What MUST NOT happen is two categories whose DEFINITIONS are essentially the same idea phrased differently.

Additional taxonomy rules:
- Discover the taxonomy from the actual content. Do NOT impose a preset list. The number of categories is YOUR judgement — use as many as the content warrants and no more.
- Each category needs:
    - `name`: a short, fully spelled-out title in Title Case (e.g. "Breach Notification & Incident Response"). Use the full name — no abbreviations, no IDs.
    - `definition`: 1–3 sentences describing exactly what kind of obligation belongs in this category, written so that an independent reader could apply it consistently.
- Category names must be DISTINCT strings (case-insensitive).

## Rules for Clause Assignments

- Assign EVERY clause to at least one category.
- Use the FULL category `name` string in the assignment (not an ID, not an abbreviation).
- A clause may have multiple categories. Include all that genuinely apply.
- Do not invent category names in the assignments that are not in the categories list.
- When in doubt whether a clause belongs to a second category, prefer to include it. Under-labeling is a greater risk than over-labeling in this system.

## Output Format

Return a single JSON object with EXACTLY these two top-level keys:

{{
  "categories": [
    {{
      "name": "<full category name in Title Case>",
      "definition": "<1-3 sentence definition>"
    }},
    ...
  ],
  "assignments": {{
    "<clause_id_1>": ["<category name>", "<category name>", ...],
    "<clause_id_2>": ["<category name>"],
    ...
  }}
}}

Return ONLY the JSON object. No prose, no markdown fences, no explanation.

## Document Clauses

The document contains {n_clauses} clauses. Each clause is shown with its clause_id and full text:

{clauses_block}
"""


class RegulatoryClassifier:
    """法規 obligation group 分類器"""

    def __init__(self, api_key: str):
        self.client = gen_runtime.build_client(API_TIMEOUT)

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(
            multiplier=RETRY_WAIT_EXPONENTIAL_MULTIPLIER,
            max=RETRY_WAIT_EXPONENTIAL_MAX
        )
    )
    def classify(self, clauses: List[Dict]) -> Dict:
        """
        一次把所有法規 clauses 餵給 LLM,要它產生 taxonomy + assignments.

        Returns:
            {"categories": [{"name", "definition"}, ...],
             "assignments": {clause_id: [category_name, ...]}}
        """
        clauses_block = "\n".join(
            f"[{c['clause_id']}] {c['text']}" for c in clauses
        )

        prompt = CLASSIFICATION_PROMPT.format(
            n_clauses=len(clauses),
            clauses_block=clauses_block,
        )

        content = gen_runtime.chat(
            self.client,
            model=LLM_MODEL,
            reasoning_effort=REASONING_EFFORT,
            messages=[
                {"role": "system",
                 "content": "You are a legal taxonomy expert producing MECE category definitions."},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )

        result = json.loads(content)
        return result


def validate_classification(result: Dict, clauses: List[Dict]) -> Dict:
    """
    對 LLM 回傳結果做基本健全性檢查並回傳警告。
    不修正內容,只回報問題,方便人類檢視。

    Returns:
        {"warnings": [...], "stats": {...}}
    """
    warnings = []
    categories = result.get("categories", [])
    assignments = result.get("assignments", {})

    # 1. categories 結構檢查
    cat_names = [c.get("name") for c in categories]
    if len(cat_names) != len(set(n.lower() for n in cat_names if n)):
        warnings.append(" Categories contain duplicate names (case-insensitive).")

    cat_name_set = set(cat_names)

    # 2. 每條 clause 是否都有分配
    clause_ids = {c["clause_id"] for c in clauses}
    assigned_ids = set(assignments.keys())

    missing = clause_ids - assigned_ids
    if missing:
        warnings.append(f" {len(missing)} clauses missing assignment: {sorted(missing)[:5]}...")

    extra = assigned_ids - clause_ids
    if extra:
        warnings.append(f" {len(extra)} assignments reference unknown clause_ids: {sorted(extra)[:5]}...")

    # 3. assignments 引用的 category 是否都在 categories 列表中
    referenced = set()
    for cats in assignments.values():
        referenced.update(cats)
    unknown_cats = referenced - cat_name_set
    if unknown_cats:
        warnings.append(f" Assignments reference unknown categories: {sorted(unknown_cats)}")

    # 4. 是否有 clause 沒分配任何 category (empty list)
    empty_assignments = [cid for cid, cats in assignments.items() if not cats]
    if empty_assignments:
        warnings.append(f" {len(empty_assignments)} clauses have empty category list: {empty_assignments[:5]}...")

    stats = {
        "n_categories": len(categories),
        "n_clauses_assigned": len(assigned_ids & clause_ids),
        "n_clauses_total": len(clause_ids),
        "avg_categories_per_clause": (
            sum(len(v) for v in assignments.values()) / len(assignments)
            if assignments else 0.0
        ),
    }

    return {"warnings": warnings, "stats": stats}


def main():
    parser = argparse.ArgumentParser(description="Step 2: Regulatory Obligation Group Classifier")
    parser.add_argument("--input", type=str, help="Parsed regulatory JSON path")
    parser.add_argument("--output-dir", type=str, help="Output directory")
    args = parser.parse_args()

    input_path = Path(args.input if args.input else INPUT_PATH)
    output_dir = Path(args.output_dir if args.output_dir else OUTPUT_DIR)

    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入文件: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)

    document_name = input_path.stem.replace("_parsed", "")
    output_path = output_dir / f"{document_name}_obligation_groups.json"

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY 環境變數,請檢查 .env 檔案")

    print(f"讀取文件: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        clauses = json.load(f)
    print(f"共有 {len(clauses)} 條 clauses")

    print(f"\n呼叫 LLM ({LLM_MODEL}) 產生 obligation group taxonomy...")
    cost_meter.configure(system="main", step="step2_obligation_classifier")
    classifier = RegulatoryClassifier(api_key)

    try:
        result = classifier.classify(clauses)
    except Exception as e:
        raise RuntimeError(f"LLM 分類失敗: {str(e)}")
    cost_meter.flush()

    # 健全性檢查
    check = validate_classification(result, clauses)
    print(f"\n分類統計:")
    for k, v in check["stats"].items():
        print(f"   {k}: {v}")

    if check["warnings"]:
        print(f"\n警告:")
        for w in check["warnings"]:
            print(f"   {w}")

    # 顯示類別概覽
    print(f"\n Discovered Categories ({len(result.get('categories', []))}):")
    for cat in result.get("categories", []):
        print(f"   - {cat.get('name', '<unnamed>')}")
        print(f"     {cat.get('definition', '<no definition>')[:120]}")

    # 組合最終輸出
    output = {
        "document": document_name,
        "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "model": LLM_MODEL,
        "stats": check["stats"],
        "categories": result.get("categories", []),
        "assignments": result.get("assignments", {}),
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    print(f"\n結果已儲存: {output_path}")


if __name__ == "__main__":
    main()
