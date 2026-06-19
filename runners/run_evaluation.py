"""
run_evaluation.py — (B) 評估 runner(效能 + 成本 + 時間)

給定一個 run_pipeline.py 產出的輸出資料夾,計算:
  - 效能:Precision / Recall / F1(逐句 containment 評估,evaluate_retrieval.py)
  - 成本:$(OpenAI 模型)或 token(local 模型),讀 <output-dir>/costs/ 的用量
  - 時間:wall-clock(讀 run_pipeline.py 寫的 step_times.json)+ 各 LLM 步驟 elapsed

主系統與 baseline **完全解耦**。`--target` 可複選(空白分隔),列出要評估哪些:
  --target system          只評估主系統(吃 <output-dir>/compliance_results/compliant.csv)
  --target naive           只跑+評估 naive LLM baseline(只需切分後輸入,不依賴 KG pipeline)
  --target rag             只跑+評估 Traditional RAG baseline(retrieve + LLM judge;judge 用
                           --gen-model,故 model-dependent;成本 = embedding + judge chat)
  --target dense           只跑+評估 Dense Retrieval baseline(純檢索、無生成 LLM,只有 embedding)
  --target passage         只跑+評估 Passage-classification baseline(段落層級 zero-shot 分類;
                           judge/分類用 --gen-model,model-dependent;無 retrieval/embedding)
  --target system naive    主系統 + naive(= 預設)
  --target all             system + naive + rag + dense + passage 全做
  (不給 --target 時 = 'system naive',即原本預設行為)

各 baseline 本身都是獨立可執行檔(evaluation/baseline_naiveLLM.py、baseline_Traditional_RAG.py、
baseline_dense_retrieval.py、baseline_passage_classification.py),本 runner 只是幫忙設好 env / 路徑後
呼叫它們,再評估其輸出。成本各自寫到 naive_usage.json / rag_usage.json / dense_usage.json /
passage_usage.json,不與主系統 main_usage.json 混。

用法(單行,PowerShell / bash 皆可):
  python run_evaluation.py --output-dir runs/gpt-5.4-mini --gen-model gpt-5.4-mini
  python run_evaluation.py --output-dir runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target rag
  python run_evaluation.py --output-dir runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target system rag dense
  python run_evaluation.py --output-dir runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target all
  python run_evaluation.py --output-dir runs/gemma4-31b --gen-model gemma4:31b --gen-backend ollama --target rag
"""

import os
import sys
import csv
import json
import argparse
import subprocess
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
EVAL = ROOT / "evaluation"
REG = "GDPR_DPA_Requirements"
CON = "Online124"

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


def read_eval_csv(path: Path) -> dict:
    out = {}
    if not path.exists():
        return out
    for row in csv.reader(open(path, encoding="utf-8-sig")):
        if len(row) == 2:
            out[row[0]] = row[1]
    return out


def load_usage(costs_dir: Path, name: str) -> dict:
    p = costs_dir / name
    return json.load(open(p, encoding="utf-8")) if p.exists() else {}


def agg_usage(usage: dict, only_prefix=None, exclude_prefix=None) -> dict:
    """彙整用量;可用 step 名稱前綴篩選(分離前段 vs 6c)。"""
    t = dict(unc=0, cached=0, out=0, emb=0, sec=0.0)
    for step, e in usage.items():
        if only_prefix and not step.startswith(only_prefix):
            continue
        if exclude_prefix and step.startswith(exclude_prefix):
            continue
        c = e.get("chat", {}) or {}
        em = e.get("embedding", {}) or {}
        t["unc"] += c.get("uncached_input", 0)
        t["cached"] += c.get("cached_input", 0)
        t["out"] += c.get("output", 0)
        t["emb"] += em.get("input", 0)
        t["sec"] += e.get("elapsed_s", 0) or 0
    return t


def make_usd(chat_p, embed_p):
    def usd(a):
        if not chat_p:
            return None
        return (a["unc"] * chat_p["input"] + a["cached"] * chat_p["cached_input"]
                + a["out"] * chat_p["output"] + a["emb"] * embed_p) / 1e6
    return usd


def run_evaluate(predictions: Path, gt: Path, out_csv: Path, env) -> dict:
    r = subprocess.run(
        [PY, "-u", "evaluate_retrieval.py", "--predictions", str(predictions),
         "--ground-truth", str(gt), "--output", str(out_csv)],
        cwd=str(EVAL), env=env)
    if r.returncode != 0:
        raise RuntimeError(f"evaluate_retrieval 失敗: {predictions}")
    return read_eval_csv(out_csv)


def main():
    ap = argparse.ArgumentParser(description="(B) 評估 runner:效能 + 成本 + 時間(system/naive/rag 解耦)")
    ap.add_argument("--output-dir", required=True, help="run_pipeline.py 的輸出資料夾")
    ap.add_argument("--gen-model", required=True, help="生成 LLM(決定 $ 定價;local 模型只有 token)")
    ap.add_argument("--gen-backend", default="openai", choices=["openai", "ollama"])
    ap.add_argument("--target", nargs="+", default=["system", "naive"],
                    choices=["system", "naive", "rag", "dense", "passage", "all"], metavar="TARGET",
                    help="要評估哪些(可複選,空白分隔):system / naive / rag(RAG+judge) / dense(純檢索) / "
                         "passage(段落分類);all = 全部。預設 'system naive'。")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--gt", default=str(EVAL / "gt" / "online124_ground_truth.csv"))
    ap.add_argument("--reg-parsed", default=str(ROOT / "inputs" / "parsed_regulatory" / f"{REG}_parsed.json"))
    ap.add_argument("--con-parsed", default=str(ROOT / "inputs" / "parsed_contracts" / f"{CON}_parsed.json"))
    args = ap.parse_args()

    # 解析要跑哪些 target(可複選;all = 全部)
    targets = set(args.target)
    if "all" in targets:
        targets = {"system", "naive", "rag", "dense", "passage"}

    OUT = Path(args.output_dir).resolve()
    COSTS = OUT / "costs"
    GT = Path(args.gt).resolve()
    cfg_path = Path(args.config)
    if not cfg_path.exists():
        raise FileNotFoundError(
            f"找不到 config.yaml:{cfg_path}\n"
            f"請先複製設定再跑(PowerShell):Copy-Item config.example.yaml config.yaml\n"
            f"(並把 .env.example 複製成 .env 填入 OPENAI_API_KEY;見 README「安裝」)")
    pricing = yaml.safe_load(open(args.config, encoding="utf-8"))["pricing"]
    embed_p = pricing["text-embedding-3-large"]["input"]
    chat_p = pricing.get(args.gen_model)   # openai 模型才有 $ 定價;local 為 None
    usd = make_usd(chat_p, embed_p)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["GEN_BACKEND"] = args.gen_backend
    env["GEN_MODEL"] = args.gen_model
    env["COST_DIR"] = str(COSTS)

    lines = []
    def emit(s=""):
        print(s)
        lines.append(s)

    emit("=" * 70)
    emit(f"(B) 評估報告  gen={args.gen_model} backend={args.gen_backend}")
    emit(f"    輸出資料夾: {OUT}")
    emit("=" * 70)

    # ---------------- 主系統 ----------------
    if "system" in targets:
        pred = OUT / "compliance_results" / "compliant.csv"
        if not pred.exists():
            raise FileNotFoundError(f"找不到 {pred};請先跑 run_pipeline.py")
        sys_eval = run_evaluate(pred, GT, OUT / "evaluation_results_system.csv", env)

        main_u = load_usage(COSTS, "main_usage.json")
        front = agg_usage(main_u, exclude_prefix="step6c")   # classifier+extract+embedding
        sixc = agg_usage(main_u, only_prefix="step6c")        # 6c reasoning
        step_times = {}
        st_path = COSTS / "step_times.json"
        if st_path.exists():
            step_times = json.load(open(st_path, encoding="utf-8"))

        emit("\n## 主系統(structured-primary 6c)")
        emit(f"- TP {sys_eval.get('True Positives (TP)')} / FP {sys_eval.get('False Positives (FP)')} "
             f"/ FN {sys_eval.get('False Negatives (FN)')}")
        emit(f"- Precision {sys_eval.get('Precision')} / Recall {sys_eval.get('Recall')} "
             f"/ F1 {sys_eval.get('F1-Score')}")
        emit("\n### 成本 / 時間(只計真正呼叫 LLM/API 的步驟)")
        if chat_p:
            emit(f"- backend=openai,以 $ 計(in {chat_p['input']}/cached {chat_p['cached_input']}"
                 f"/out {chat_p['output']};embed-large {embed_p})")
            emit(f"  - 共用前段(classifier+extraction+embedding): ${usd(front):.6f} | {front['sec']:.1f}s")
            emit(f"  - 6c reasoning: ${usd(sixc):.6f} | {sixc['sec']:.1f}s")
            emit(f"  - 主系統整條 = ${usd(front)+usd(sixc):.6f}")
        else:
            emit("- backend=ollama(local),無 $ 定價 -> 以 token + 時間計:")
            emit(f"  - 共用前段(gen=local;embedding 仍 OpenAI): "
                 f"gen tokens(unc {front['unc']} / out {front['out']}) embed {front['emb']} | {front['sec']:.1f}s")
            emit(f"  - 6c reasoning: tokens(unc {sixc['unc']} / out {sixc['out']}) | {sixc['sec']:.1f}s")
            emit("  (注:embedding 一律 OpenAI text-embedding-3-large。)")
        if step_times:
            emit(f"\n### 全流程 wall-clock(每步;含純 code)— 總 {step_times.get('total_s', 0):.1f}s "
                 f"({step_times.get('total_s', 0)/60:.1f} 分鐘)")
            for s in step_times.get("steps", []):
                emit(f"  - {s['label']:<22}: {s['seconds']:8.1f}s")

    # ---------------- Naive baseline(解耦) ----------------
    if "naive" in targets:
        naive_dir = OUT / "baselines" / "naive_baseline"
        naive_dir.mkdir(parents=True, exist_ok=True)
        naive_csv = naive_dir / "naiveLLM_compliant.csv"
        emit("\n## Naive baseline(獨立執行,僅需切分後輸入)")
        r = subprocess.run(
            [PY, "-u", "baseline_naiveLLM.py",
             "--reg-parsed", str(Path(args.reg_parsed).resolve()),
             "--con-parsed", str(Path(args.con_parsed).resolve()),
             "--out", str(naive_csv)],
            cwd=str(EVAL), env=env)
        if r.returncode != 0:
            raise RuntimeError("baseline_naiveLLM 失敗")
        naive_eval = run_evaluate(naive_csv, GT, naive_dir / "evaluation_results.csv", env)
        naive_u = load_usage(COSTS, "naive_usage.json")
        nv = agg_usage(naive_u)
        emit(f"- Precision {naive_eval.get('Precision')} / Recall {naive_eval.get('Recall')} "
             f"/ F1 {naive_eval.get('F1-Score')}")
        if chat_p:
            emit(f"- 成本: ${usd(nv):.6f} | {nv['sec']:.1f}s")
        else:
            emit(f"- 成本: tokens(unc {nv['unc']} / out {nv['out']}) | {nv['sec']:.1f}s")

    # ---------------- Traditional RAG baseline(解耦,retrieve + LLM judge,model-dependent)----------------
    if "rag" in targets:
        rag_dir = OUT / "baselines" / "rag_baseline"
        rag_dir.mkdir(parents=True, exist_ok=True)
        rag_csv = rag_dir / "Traditional_RAG_compliant.csv"
        emit("\n## Traditional RAG baseline(retrieve + LLM judge;judge 用 --gen-model,model-dependent)")
        r = subprocess.run(
            [PY, "-u", "baseline_Traditional_RAG.py",
             "--reg-parsed", str(Path(args.reg_parsed).resolve()),
             "--con-parsed", str(Path(args.con_parsed).resolve()),
             "--out", str(rag_csv)],
            cwd=str(EVAL), env=env)
        if r.returncode != 0:
            raise RuntimeError("baseline_Traditional_RAG 失敗")
        rag_eval = run_evaluate(rag_csv, GT, rag_dir / "evaluation_results.csv", env)
        rag_u = load_usage(COSTS, "rag_usage.json")
        rg = agg_usage(rag_u)   # embedding(rag_embed)+ judge chat(rag_judge)
        rag_embed_cost = rg["emb"] * embed_p / 1e6
        emit(f"- Precision {rag_eval.get('Precision')} / Recall {rag_eval.get('Recall')} "
             f"/ F1 {rag_eval.get('F1-Score')}")
        if chat_p:
            # usd(rg) = embedding $ + judge chat $(用 --gen-model 的 pricing)
            emit(f"- 成本(embedding + judge chat): ${usd(rg):.6f} | {rg['sec']:.1f}s "
                 f"(embed {rg['emb']} tok=${rag_embed_cost:.6f};judge chat unc {rg['unc']}/out {rg['out']})")
        else:
            emit(f"- 成本(local judge):judge chat tokens(unc {rg['unc']} / out {rg['out']}) "
                 f"+ embedding ${rag_embed_cost:.6f} | {rg['sec']:.1f}s")

    # ---------------- Dense Retrieval baseline(解耦,純檢索、無生成 LLM)----------------
    if "dense" in targets:
        dense_dir = OUT / "baselines" / "dense_baseline"
        dense_dir.mkdir(parents=True, exist_ok=True)
        dense_csv = dense_dir / "dense_retrieval_compliant.csv"
        emit("\n## Dense Retrieval baseline(純檢索,僅需切分後輸入;無生成 LLM)")
        r = subprocess.run(
            [PY, "-u", "baseline_dense_retrieval.py",
             "--reg-parsed", str(Path(args.reg_parsed).resolve()),
             "--con-parsed", str(Path(args.con_parsed).resolve()),
             "--out", str(dense_csv)],
            cwd=str(EVAL), env=env)
        if r.returncode != 0:
            raise RuntimeError("baseline_dense_retrieval 失敗")
        dense_eval = run_evaluate(dense_csv, GT, dense_dir / "evaluation_results.csv", env)
        dense_u = load_usage(COSTS, "dense_usage.json")
        dn = agg_usage(dense_u)
        # dense 只有 embedding(一律 OpenAI 計價,與生成模型無關)
        dense_cost = dn["emb"] * embed_p / 1e6
        emit(f"- Precision {dense_eval.get('Precision')} / Recall {dense_eval.get('Recall')} "
             f"/ F1 {dense_eval.get('F1-Score')}")
        emit(f"- 成本(只有 embedding): ${dense_cost:.6f} | {dn['sec']:.1f}s "
             f"(embed {dn['emb']} tok @ {embed_p}/1M)")

    # ---------------- Passage-classification baseline(解耦,段落 zero-shot 分類,model-dependent)----------------
    if "passage" in targets:
        passage_dir = OUT / "baselines" / "passage_baseline"
        passage_dir.mkdir(parents=True, exist_ok=True)
        passage_csv = passage_dir / "passage_classification_compliant.csv"
        emit("\n## Passage-classification baseline(段落層級 zero-shot 分類;用 --gen-model,無 retrieval/embedding)")
        r = subprocess.run(
            [PY, "-u", "baseline_passage_classification.py",
             "--con-parsed", str(Path(args.con_parsed).resolve()),
             "--out", str(passage_csv)],
            cwd=str(EVAL), env=env)
        if r.returncode != 0:
            raise RuntimeError("baseline_passage_classification 失敗")
        pass_eval = run_evaluate(passage_csv, GT, passage_dir / "evaluation_results.csv", env)
        pass_u = load_usage(COSTS, "passage_usage.json")
        pg = agg_usage(pass_u)   # 只有 chat(每段一次分類 call),無 embedding
        emit(f"- Precision {pass_eval.get('Precision')} / Recall {pass_eval.get('Recall')} "
             f"/ F1 {pass_eval.get('F1-Score')}")
        if chat_p:
            emit(f"- 成本(分類 chat): ${usd(pg):.6f} | {pg['sec']:.1f}s "
                 f"(chat unc {pg['unc']}/out {pg['out']})")
        else:
            emit(f"- 成本(local 分類):chat tokens(unc {pg['unc']} / out {pg['out']}) | {pg['sec']:.1f}s")

    emit("\n" + "=" * 70)
    report = OUT / "REPORT_eval.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[report] 已存 {report}")


if __name__ == "__main__":
    main()
