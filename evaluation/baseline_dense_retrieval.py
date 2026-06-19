"""
baseline_dense_retrieval.py — Dense Retrieval baseline(純檢索,無 LLM judge)

法規逐條當 query,合約 clause(parser 切的)進向量庫,每條 query retrieve TopK 最相似的
合約 clause,直接當對應輸出。不加 LLM judge(純檢索)。

- 輸入:只用切分後(step1 parser)的產物,與主系統 / naive / RAG 同一份
    法規 query = inputs/parsed_regulatory/*_parsed.json(R 條)
    合約 chunk = inputs/parsed_contracts/*_parsed.json(parser 切的 clause)
- 純檢索,**沒有任何生成 LLM 呼叫**;只用 embedding(OpenAI text-embedding-3-large)。
- TopK = 5;threshold 固定 0.6(只保留 top-5 中 cosine >= 0.6 者)。
- 輸出:evaluate_retrieval.py 吃的 compliant.csv 格式。
- 成本:system="dense" -> dense_usage.json(只有 embedding,不與 main/naive/rag 混)。

使用方式(獨立可執行):
    python baseline_dense_retrieval.py --reg-parsed <reg_parsed.json> --con-parsed <con_parsed.json> --out <out.csv>
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
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost_meter


# ==================== 實驗參數 ====================
EMBEDDING_MODEL = "text-embedding-3-large"
TOPK = 5
THRESHOLD = 0.4

REG_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_regulatory" / "GDPR_DPA_Requirements_parsed.json")
CON_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_contracts" / "Online124_parsed.json")
OUTPUT_CSV = "baselines/dense_retrieval_compliant.csv"
API_TIMEOUT = 60
EMBED_BATCH = 100
# ===================================================


def _resolve(base: Path, p: str) -> Path:
    # relative 路徑以「目前工作目錄(CWD)」為基準(符合 README 從 release/ 跑的範例);absolute 照用。
    # 預設值已是絕對路徑,故 base 不再使用(保留參數相容)。
    pp = Path(p)
    return pp if pp.is_absolute() else (Path.cwd() / pp)


def embed_texts(client, texts):
    out = []
    for i in range(0, len(texts), EMBED_BATCH):
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=texts[i:i + EMBED_BATCH])
        cost_meter.add_embedding(resp)
        out.extend([d.embedding for d in resp.data])
    return np.array(out)


def main():
    parser = argparse.ArgumentParser(description="Dense Retrieval baseline(純檢索,獨立可執行)")
    parser.add_argument("--topk", type=int, default=TOPK)
    parser.add_argument("--threshold", type=float, default=THRESHOLD)
    parser.add_argument("--reg-parsed", default=REG_PARSED, help="切分後法規 JSON(step1 輸出)")
    parser.add_argument("--con-parsed", default=CON_PARSED, help="切分後合約 JSON(step1 輸出)")
    parser.add_argument("--out", default=OUTPUT_CSV, help="輸出 compliant.csv 路徑")
    args = parser.parse_args()

    base = Path(__file__).parent
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY。請複製 .env.example -> .env(PowerShell:Copy-Item .env.example .env)並填入 key;見 README「安裝」。")

    reg = json.load(open(_resolve(base, args.reg_parsed), encoding="utf-8"))
    con = json.load(open(_resolve(base, args.con_parsed), encoding="utf-8"))
    reg_q = [{"rule_id": c["clause_id"], "text": re.sub(r'^\s*[-–—]\s*', '', c["text"])} for c in reg]
    con_c = [{"clause_id": c["clause_id"], "text": c["text"]} for c in con]
    print(f"法規 query: {len(reg_q)} | 合約 chunk: {len(con_c)} | TopK={args.topk} | threshold={args.threshold} | embed={EMBEDDING_MODEL}")

    client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)
    cost_meter.configure(system="dense", step="dense_embed")
    print("embedding 合約 chunk + 法規 query ...")
    con_emb = embed_texts(client, [c["text"] for c in con_c])
    reg_emb = embed_texts(client, [q["text"] for q in reg_q])
    cost_meter.flush()

    sims = reg_emb @ con_emb.T   # embedding 已正規化,點積即 cosine

    rows = []
    for i, q in enumerate(reg_q):
        for j in np.argsort(-sims[i])[:args.topk]:
            score = float(sims[i, j])
            if score >= args.threshold:
                rows.append([q["rule_id"], con_c[j]["clause_id"], con_c[j]["text"], f"{score:.4f}"])

    out = _resolve(base, args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "contract_clause_id", "retrieved_sentence", "retrieval_score"])
        w.writerows(rows)
    print(f"輸出 {len(rows)} 列(TopK={args.topk}, threshold={args.threshold})-> {out}")


if __name__ == "__main__":
    main()
