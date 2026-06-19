"""
run_ablation_6c.py — 「只換 6c 輸入型態」的控制變因 ablation 執行器。

重用 canonical 已跑好的資料(每個 run 的 stage_b_pairs.json = 進 6c 的配對 + norms),
**只重跑 6c**(換不同變體的 prompt/輸入),不重跑任何前置流程。三個要跑的變體:
structured_only / text_only / hybrid_textprimary(prompt 見 src_ablation/ablation_prompts.py,
共用段對齊現行 main)。main 變體不在此跑 —— 直接用 canonical 現成 n=3 結果。

每變體 × 每合約 × 每 run 各跑一次 6c → aggregate → 同一支 containment 評估器算 P/R/F1。
輸出到 results_ablation/<variant>/<contract>/run<N>/,不覆蓋 canonical、不互相覆蓋。可續跑
(已產出 eval 的 (variant,contract,run) 會跳過)。temp 0、threshold 0.40 不變。

用法(從 /release):python runners/run_ablation_6c.py
"""
import os, sys, json, argparse, subprocess
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import threading

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

sys.path.insert(0, str(ROOT / "src" / "6_matching"))
sys.path.insert(0, str(ROOT / "src_ablation"))
sys.path.insert(0, str(ROOT / "lib"))
import importlib.util as _u
# 只透過 ablation_prompts 載入 stage_c_reasoning 一次(它會包 stdout);runner 重用同一份。
_a = _u.spec_from_file_location("ap", ROOT / "src_ablation" / "ablation_prompts.py")
ap = _u.module_from_spec(_a); _a.loader.exec_module(ap)
scr = ap.MAIN_MODULE
import gen_runtime, cost_meter

REG = "GDPR_DPA_Requirements"
CONTRACTS = ["Online124", "Online39"]
RESULTS = ROOT / "results"
OUT_ROOT = ROOT / "results_ablation"
PARENT_FIELDS = scr.PARENT_CONTEXT_FIELDS


def load_norms(p):
    d = json.load(open(p, encoding="utf-8"))
    lst = d if isinstance(d, list) else d.get("norms", list(d.values()))
    return {n["clause_id"]: n for n in lst}


def parent_ctx(mode, con_parent):
    if not con_parent:
        return ""
    if mode == "struct":
        lines = ["", "## Parent clause context (structured fields)",
                 "The contract obligation above is a SUB-ITEM of a larger parent clause. Treat the "
                 "sub-item TOGETHER WITH this parent context as the contract's fulfillment of the requirement."]
        for f in PARENT_FIELDS:
            lines.append(f"{f}: {scr._fmt(con_parent.get(f))}")
        return "\n".join(lines)
    if mode == "text":
        return ("\n## Parent clause context (original text)\n"
                "The contract obligation above is a SUB-ITEM of a larger parent clause. Treat the "
                "sub-item TOGETHER WITH this parent context as the contract's fulfillment of the requirement.\n"
                f"Original text: {scr._fmt(con_parent.get('source_text'))}")
    return scr.build_parent_context(con_parent)   # both:結構化 + 原文(= main)


def build_prompt(prompt, reg, con, con_parent, parent_mode):
    p = prompt
    for f in scr.STRUCT_FIELDS:
        p = p.replace("{reg_" + f + "}", scr._fmt(reg.get(f)))
        p = p.replace("{contract_" + f + "}", scr._fmt(con.get(f)))
    p = p.replace("{reg_source_text}", scr._fmt(reg.get("source_text")))
    p = p.replace("{contract_source_text}", scr._fmt(con.get("source_text")))
    p = p.replace("{parent_context}", parent_ctx(parent_mode, con_parent))
    return p


def run_6c(prompt, parent_mode, pairs, reg_norms, con_norms, client, workers=8):
    prog = {"n": 0}; lock = threading.Lock(); total = len(pairs)

    def work(p):
        r_id, c_id = p["reg_clause_id"], p["contract_clause_id"]
        reg = reg_norms.get(r_id); con = con_norms.get(c_id)
        if not reg or not con:
            return None
        pid = con.get("belongs_to") or con.get("parent")
        con_parent = con_norms.get(pid) if pid else None
        prompt_full = build_prompt(prompt, reg, con, con_parent, parent_mode)
        content = gen_runtime.chat(client, model=scr.LLM_MODEL, reasoning_effort=scr.REASONING_EFFORT,
                                   messages=[{"role": "system", "content": "You are a compliance entailment reviewer."},
                                             {"role": "user", "content": prompt_full}],
                                   temperature=scr.TEMPERATURE, response_format={"type": "json_object"})
        v = json.loads(content)
        with lock:
            prog["n"] += 1
            if prog["n"] % 100 == 0 or prog["n"] == total:
                print(f"      {prog['n']}/{total}", flush=True)
        return {"reg_clause_id": r_id, "contract_clause_id": c_id, "reg_belongs_to": reg.get("belongs_to"),
                "rule_id": reg.get("belongs_to") or r_id, "contract_origin_clause": con.get("origin_clause") or c_id,
                "parent_context_used": bool(con_parent), "similarity": p.get("similarity"),
                "shared_groups": p.get("shared_groups", []), "verdict": v.get("verdict"),
                "core_alignment_check": v.get("core_alignment_check"), "condition_check": v.get("condition_check"),
                "modality_check": v.get("modality_check"), "other_constraints_check": v.get("other_constraints_check"),
                "contract_source_text": con.get("source_text")}

    with ThreadPoolExecutor(max_workers=workers) as ex:
        res = [r for r in ex.map(work, pairs) if r]
    return res


def main():
    ap_ = argparse.ArgumentParser()
    ap_.add_argument("--workers", type=int, default=8)
    args = ap_.parse_args()
    cost_meter.configure(system="ablation", step="ablation_6c")
    client = gen_runtime.build_client(60)

    plan = [(v, c, r) for v in ap.VARIANTS for c in CONTRACTS for r in (1, 2, 3)]
    print(f"ABLATION 6c — {len(plan)} 個 (variant×contract×run) 單位待跑(已完成者跳過)")
    from collections import Counter
    for variant, contract, run in plan:
        src = RESULTS / contract / f"run{run}" / "system"
        out = OUT_ROOT / variant / contract / f"run{run}"
        evcsv = out / "eval" / "evaluation_results.csv"
        if evcsv.exists():
            print(f"  [skip] {variant}/{contract}/run{run}(已完成)")
            continue
        print(f"\n  ▶ {variant} / {contract} / run{run}")
        (out / "compliance_results").mkdir(parents=True, exist_ok=True)
        (out / "eval").mkdir(parents=True, exist_ok=True)
        os.environ["COST_DIR"] = str(out / "cost")
        reg_norms = load_norms(src / "norms" / f"{REG}_norms.json")
        con_norms = load_norms(src / "norms" / f"{contract}_norms.json")
        pairs = json.load(open(src / "compliance_results" / "stage_b_pairs.json", encoding="utf-8"))["pairs"]
        vcfg = ap.VARIANTS[variant]
        results = run_6c(vcfg["prompt"], vcfg["parent"], pairs, reg_norms, con_norms, client, args.workers)
        vc = Counter(r["verdict"] for r in results)
        json.dump({"n_input": len(pairs), "n_judged": len(results), "verdict_counts": dict(vc), "results": results},
                  open(out / "compliance_results" / "stage_c_results.json", "w", encoding="utf-8"), indent=2, ensure_ascii=False)
        # aggregate
        subprocess.run([PY, str(ROOT / "src" / "7_aggregation" / "aggregator.py"),
                        "--input", str(out / "compliance_results" / "stage_c_results.json"),
                        "--output-dir", str(out / "compliance_results"),
                        "--reg-norms", str(src / "norms" / f"{REG}_norms.json")], check=True)
        # evaluate (same containment evaluator; absolute paths)
        gt = ROOT / "evaluation" / "gt" / f"{contract.lower()}_ground_truth.csv"
        subprocess.run([PY, str(ROOT / "evaluation" / "evaluate_retrieval.py"),
                        "--predictions", str(out / "compliance_results" / "compliant.csv"),
                        "--ground-truth", str(gt), "--output", str(evcsv)], check=True)
        print(f"    ✓ {variant}/{contract}/run{run}  verdicts={dict(vc)}")
    print("\nABLATION 6c 全部完成。接著:python runners/build_ablation_report.py")


if __name__ == "__main__":
    main()
