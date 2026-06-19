"""
baseline_Traditional_RAG.py — Traditional RAG baseline(retrieve + LLM-as-judge)

流程:法規逐條當 query → 合約 clause 進向量庫 → 每條 retrieve TopK(過 retrieval threshold)
→ 把該要求 + 候選一起丟給 judge LLM 判哪些真的滿足 → 只留 judge 認可的 → compliant.csv。
judge 是 **per-requirement**(每條要求一個 call,連同它的候選一起判),不是 per-pair。

release 慣例:
- embedding:OpenAI text-embedding-3-large(與主系統 / naive / dense 同一個 embedding model)。
  **每次跑都從頭 embed,不快取、不與主 pipeline 或其他 run 共用**(批次多模型各自重 embed)。
- judge LLM:走 lib/gen_runtime.chat()(GEN_MODEL / GEN_BACKEND env 可換生成模型;ollama 亦可)。
- reasoning effort:REASONING_EFFORT=None,繼承 lib/gen_runtime 的集中 DEFAULT(目前 "none")。
- 成本:system="rag" -> rag_usage.json,兩步:rag_embed(embedding)+ rag_judge(judge chat,
  含 token 與耗時)。與 main / naive / dense 各自分開,不互相覆蓋。
- 加了 judge 後本 baseline **是 model-dependent 的**(judge 用哪個生成模型會影響結果與成本)。

使用方式(獨立可執行;生成模型由 GEN_MODEL/GEN_BACKEND env 控制,預設 JUDGE_MODEL):
    python baseline_Traditional_RAG.py --reg-parsed <reg_parsed.json> --con-parsed <con_parsed.json> --out <out.csv>
"""

import os
import csv
import json
import sys
import re
import argparse
from pathlib import Path
import numpy as np
from dotenv import load_dotenv
from openai import OpenAI

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost_meter
import gen_runtime


# ==================== 實驗參數 ====================
EMBEDDING_MODEL = "text-embedding-3-large"
JUDGE_MODEL = "gpt-5.4-mini"      # 預設 judge 模型;GEN_MODEL env 會覆蓋(與 naive 同慣例)
REASONING_EFFORT = None           # None = 繼承 lib/gen_runtime 的 DEFAULT_REASONING_EFFORT(目前 "none")
TEMPERATURE = 0.0
TOPK = 5                          # 每條要求 retrieve 幾個候選給 judge
RETRIEVAL_THRESHOLD = 0.45        # 候選 cosine 門檻(設成跟系統 6b 同值,讓檢索起點公平)
EMBED_BATCH = 100
API_TIMEOUT = 180

REG_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_regulatory" / "GDPR_DPA_Requirements_parsed.json")
CON_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_contracts" / "Online124_parsed.json")
OUTPUT_CSV = "baselines/Traditional_RAG_compliant.csv"
# ===================================================

JUDGE_PROMPT = """You are checking ONE regulatory requirement against a shortlist of candidate contract clauses retrieved for it.

Requirement (%RID%):
%REQ%

Candidate contract clauses:
%CANDIDATES%

Decide which candidate clauses DIRECTLY satisfy / fulfill this requirement with an active contractual commitment.
- Do NOT count clauses that merely list examples, terms, or nouns that happen to fit the description without an active commitment.
- A clause that is only topically related but does not actually satisfy the requirement should be excluded.
- If none of the candidates satisfy it, return an empty list.

Return ONLY a JSON object, no prose, no markdown fences:
{"satisfying_clause_ids": ["<clause_id>", ...]}"""


def _resolve(base: Path, p: str) -> Path:
    # relative 路徑以「目前工作目錄(CWD)」為基準(符合 README 從 release/ 跑的範例);absolute 照用。
    # 預設值已是絕對路徑,故 base 不再使用(保留參數相容)。
    pp = Path(p)
    return pp if pp.is_absolute() else (Path.cwd() / pp)


def embed_texts(client, texts):
    """embedding 一律 OpenAI(直接 client),用 cost_meter 記 embedding 用量。"""
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts[i:i + EMBED_BATCH])
        cost_meter.add_embedding(resp)
        out.extend([d.embedding for d in resp.data])
    return np.array(out)


def parse_ids(text):
    t = (text or "").strip()
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    try:
        obj = json.loads(t)
        ids = obj.get("satisfying_clause_ids", [])
        return [str(x) for x in ids] if isinstance(ids, list) else []
    except Exception:
        m = re.search(r'"satisfying_clause_ids"\s*:\s*\[(.*?)\]', t, re.S)
        return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def main():
    ap = argparse.ArgumentParser(description="Traditional RAG baseline(retrieve + LLM judge,獨立可執行)")
    ap.add_argument("--reg-parsed", default=REG_PARSED, help="切分後法規 JSON(step1 輸出)")
    ap.add_argument("--con-parsed", default=CON_PARSED, help="切分後合約 JSON(step1 輸出)")
    ap.add_argument("--out", default=OUTPUT_CSV, help="輸出 compliant.csv 路徑")
    ap.add_argument("--topk", type=int, default=TOPK)
    ap.add_argument("--threshold", type=float, default=RETRIEVAL_THRESHOLD, help="候選 retrieval cosine 門檻")
    args = ap.parse_args()

    base = Path(__file__).parent
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY。請複製 .env.example -> .env(PowerShell:Copy-Item .env.example .env)並填入 key;見 README「安裝」。")

    reg = json.load(open(_resolve(base, args.reg_parsed), encoding="utf-8"))
    con = json.load(open(_resolve(base, args.con_parsed), encoding="utf-8"))
    reg_q = [{"rule_id": c["clause_id"], "text": re.sub(r'^\s*[-–—]\s*', '', c["text"])} for c in reg]
    con_c = [{"clause_id": c["clause_id"], "text": c["text"]} for c in con]
    print(f"法規 query: {len(reg_q)} | 合約 chunk: {len(con_c)} | TopK={args.topk} | "
          f"retrieval_threshold={args.threshold} | judge={gen_runtime.resolve_model(JUDGE_MODEL)} "
          f"backend={gen_runtime.backend()}")

    # ---- 1) 檢索:embedding 一律 OpenAI(每次從頭 embed,不快取) ----
    embed_client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)
    cost_meter.configure(system="rag", step="rag_embed")
    print("embedding 合約 chunk + 法規 query ...")
    con_emb = embed_texts(embed_client, [c["text"] for c in con_c])
    reg_emb = embed_texts(embed_client, [q["text"] for q in reg_q])
    cost_meter.flush()
    sims = reg_emb @ con_emb.T   # OpenAI embedding 已正規化,點積即 cosine

    # ---- 2) judge:per-requirement,走 gen_runtime(GEN_MODEL/GEN_BACKEND) ----
    gen_client = gen_runtime.build_client(API_TIMEOUT)
    cost_meter.configure(system="rag", step="rag_judge")
    rows, n_cand_total, n_fail = [], 0, 0
    for i, q in enumerate(reg_q):
        cands = []
        for j in np.argsort(-sims[i])[:args.topk]:
            score = float(sims[i, j])
            if score >= args.threshold:
                cands.append((con_c[j]["clause_id"], con_c[j]["text"], score))
        if not cands:
            continue
        n_cand_total += len(cands)
        cand_block = "\n".join(f"- [{cid}] {txt}" for cid, txt, _ in cands)
        prompt = (JUDGE_PROMPT.replace("%RID%", q["rule_id"])
                              .replace("%REQ%", q["text"])
                              .replace("%CANDIDATES%", cand_block))
        try:
            content = gen_runtime.chat(
                gen_client, model=JUDGE_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=TEMPERATURE,
                response_format={"type": "json_object"},
                reasoning_effort=REASONING_EFFORT,
            )
            kept = set(parse_ids(content))
        except Exception as e:
            print(f"  [judge 失敗] {q['rule_id']}: {e}")
            kept, n_fail = set(), n_fail + 1
        n_keep = 0
        for cid, txt, score in cands:
            if cid in kept:
                rows.append([q["rule_id"], cid, txt, f"{score:.4f}"])
                n_keep += 1
        print(f"  {q['rule_id']}: 候選 {len(cands)} -> judge 留 {n_keep}")
    cost_meter.flush()

    out = _resolve(base, args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "contract_clause_id", "retrieved_sentence", "retrieval_score"])
        w.writerows(rows)
    print(f"\n候選總數 {n_cand_total} -> judge 後輸出 {len(rows)} 列 | judge 失敗 {n_fail} 條 -> {out}")


if __name__ == "__main__":
    main()
