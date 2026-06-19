"""
Baseline 1: Naive LLM

最少人工介入:把「切分後的法規條目 + 切分後的合約條目」(step1 parser 的輸出,
與 RAG / 主系統共用同一份切分) 一次餵給同一個 LLM(gpt-5.4-mini),一次呼叫,
直接讓 LLM 輸出每條法規對應到哪些合約句子(逐字)。

設計說明:
- 輸入用 output/parsed_* 的 parsed json(step1 切分),與 baseline RAG、主系統的
  合約表示「同一份」,使三系統評估時的句子粒度一致。
- LLM 一次看完兩邊全部條目後自由輸出對應句,只有單次 chat 呼叫。
- 以 prompt 約束輸出粒度:每句來自單一合約條目、不跨條目串整段、多句分開列出。
- 輸出:evaluate_retrieval.py 吃的 compliant.csv 格式。每句給唯一 contract_clause_id,
  避免 evaluate 的 (rule_id, contract_clause_id) 去重把多句壓成一句。
- 成本:system="naive",一次 chat 呼叫,token 取自 usage。

使用方式:
    python baseline_naive.py
"""

import os
import csv
import json
import sys
import argparse
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost_meter
import gen_runtime


# ==================== 實驗參數 ====================
LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
API_TIMEOUT = 180

REG_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_regulatory" / "GDPR_DPA_Requirements_parsed.json")
CON_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_contracts" / "Online124_parsed.json")
# model swap:輸出路徑可由 env NAIVE_OUT_CSV 覆蓋(預設原本位置)。生成模型由 GEN_MODEL/GEN_BACKEND 切換。
OUTPUT_CSV = os.environ.get("NAIVE_OUT_CSV", "baselines/naiveLLM_compliant.csv")
# ===================================================


PROMPT = """You are given the regulation as a list of requirements and the contract as a list of clauses.

For EACH regulation requirement Rn, run a compliance check:

- Identify the contract sentence(s) that directly satisfy or match the requirement.
- Do not include sentences that merely list examples, terms, or nouns that happen to fit the regulation's description without an active contractual commitment.
- Single Sentence Focus: For each match, output only the single most direct sentence. Do not concatenate multiple sentences or dump entire paragraphs.
- If a requirement matches multiple separate clauses, list each matching sentence as a separate item.

Return ONLY a JSON object:
{
  "matches": [
    {"rule_id": "R1", "verdict": "Compliant",
     "contract_sentences": ["<verbatim>"]},
    ...
  ]
}
Only include requirements you judge Compliant. No prose, no markdown fences.

## REGULATION
%REG%

## CONTRACT
%CON%
"""


def _fmt_items(items):
    lines = []
    for it in items:
        cid = it.get("clause_id", "")
        text = (it.get("text") or "").strip()
        lines.append(f"{cid}: {text}")
    return "\n".join(lines)


def _resolve(base: Path, p: str) -> Path:
    # relative 路徑以「目前工作目錄(CWD)」為基準(符合 README 從 release/ 跑的範例);absolute 照用。
    # 預設值已是絕對路徑,故 base 不再使用(保留參數相容)。
    pp = Path(p)
    return pp if pp.is_absolute() else (Path.cwd() / pp)


def main():
    base = Path(__file__).parent
    ap = argparse.ArgumentParser(description="Baseline: Naive LLM(單次呼叫,獨立可執行)")
    ap.add_argument("--reg-parsed", default=REG_PARSED, help="切分後法規 JSON(step1 輸出)")
    ap.add_argument("--con-parsed", default=CON_PARSED, help="切分後合約 JSON(step1 輸出)")
    ap.add_argument("--out", default=OUTPUT_CSV, help="輸出 compliant.csv 路徑")
    args = ap.parse_args()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY。請複製 .env.example -> .env(PowerShell:Copy-Item .env.example .env)並填入 key;見 README「安裝」。")

    reg_items = json.loads(_resolve(base, args.reg_parsed).read_text(encoding="utf-8"))
    con_items = json.loads(_resolve(base, args.con_parsed).read_text(encoding="utf-8"))

    reg_block = _fmt_items(reg_items)
    con_block = _fmt_items(con_items)
    print(f"法規條目: {len(reg_items)} | 合約條目: {len(con_items)}")

    prompt = PROMPT.replace("%REG%", reg_block).replace("%CON%", con_block)

    client = gen_runtime.build_client(API_TIMEOUT)
    cost_meter.configure(system="naive", step="naive")
    print(f"呼叫 生成模型(單次,切分後法規+合約)... model={gen_runtime.resolve_model(LLM_MODEL)} backend={gen_runtime.backend()}")
    content = gen_runtime.chat(
        client,
        model=LLM_MODEL,
        reasoning_effort=REASONING_EFFORT,
        messages=[
            {"role": "system", "content": "You are a compliance reviewer."},
            {"role": "user", "content": prompt},
        ],
        temperature=TEMPERATURE,
        response_format={"type": "json_object"},
    )
    cost_meter.flush()

    result = json.loads(content)
    matches = result.get("matches", []) or []

    rows = []
    for m in matches:
        rule_id = (m.get("rule_id") or "").strip()
        if not rule_id:
            continue
        sents = m.get("contract_sentences", []) or []
        for i, s in enumerate(sents):
            if s and s.strip():
                # 每句唯一 contract_clause_id,避免 evaluate 去重把多句壓成一句
                rows.append([rule_id, f"{rule_id}_n{i}", s.strip(), ""])

    out = _resolve(base, args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "contract_clause_id", "retrieved_sentence", "retrieval_score"])
        w.writerows(rows)

    n_rules = len({r[0] for r in rows})
    print(f"輸出 {len(rows)} 列(涵蓋 {n_rules} 條法規)-> {out}")


if __name__ == "__main__":
    main()