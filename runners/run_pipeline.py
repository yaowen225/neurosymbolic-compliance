"""
run_pipeline.py — (A) 主系統 pipeline runner

給定生成模型 + 參數(threshold / 輸出資料夾 / Neo4j DB),把主系統整條跑完:
  step1 parser(txt -> parsed json)-> step2 classifier
  -> step3 extract(reg/con,含併入的 core_sentence 後處理)
  -> step4 embedding -> step5 KG -> 6a group filter -> (threshold) -> 6b semantic
  -> 6c reasoning(stage_c_reasoning.py)-> step7 aggregate

只跑到產生 compliant.csv 為止,**不做評估**(評估請用 run_evaluation.py)。
每一步的 token 用量 + wall-clock 都記到 <output-dir>/costs/。所有輸入/輸出都吃明確路徑,
全部落在 --output-dir 底下,不會碰到舊的 output/。

說明:
- 只換生成 LLM(--gen-model / --gen-backend);embedding 固定 OpenAI text-embedding-3-large。
- step1 預設會跑(吃 inputs/raw/ 的原始 txt,parse 到 <output-dir>/parsed_*)。
  raw 已驗證 parse 出來與 inputs/parsed_* 完全一致。要沿用既有 parsed、跳過 step1:加 --skip-step1。
- 會自動把 config.yaml 的 neo4j.database 改成 --db(等同手動切 DB)。Neo4j 由你自行清空。

用法(PowerShell / bash 皆可直接貼,單行):
  python run_pipeline.py --gen-model gpt-5.4-mini --gen-backend openai --db exp1 --output-dir runs/gpt-5.4-mini --threshold 0.45
  python run_pipeline.py --gen-model gpt-5.4-mini --gen-backend openai --db exp1 --output-dir runs/gpt-5.4-mini --threshold 0.45 --skip-step1
  python run_pipeline.py --gen-model gemma4:31b --gen-backend ollama --db exp3 --output-dir runs/gemma4-31b   # 不給 --threshold 則 autotune
"""

import os
import re
import sys
import json
import time
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable
SRC = ROOT / "src"
EVAL = ROOT / "evaluation"
TOOLS = ROOT / "tools"
REG = "GDPR_DPA_Requirements"
CON = "Online124"

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

STEP_TIMES = []   # [(label, seconds)] 全流程每步 wall-clock(含純 code 步驟;不含 threshold 測試)


def set_db(config_path: Path, db: str):
    cfg = config_path.read_text(encoding="utf-8")
    cfg2 = re.sub(r'(database:\s*")[^"]*(")', r"\g<1>" + db + r"\g<2>", cfg, count=1)
    config_path.write_text(cfg2, encoding="utf-8")
    print(f"[config] neo4j.database -> {db}")


def run(cwd, args, env, label):
    print(f"\n>>> [{label}] ({cwd.relative_to(ROOT)}) {' '.join(str(a) for a in args)}")
    t0 = time.time()
    r = subprocess.run([PY, "-u"] + [str(a) for a in args], cwd=str(cwd), env=env)
    dt = time.time() - t0
    STEP_TIMES.append((label, dt))
    print(f"    [{label}] 耗時 {dt:.1f}s")
    if r.returncode != 0:
        raise RuntimeError(f"步驟失敗 (rc={r.returncode}): {args}")


def main():
    ap = argparse.ArgumentParser(description="(A) 主系統 pipeline runner -> compliant.csv")
    ap.add_argument("--gen-model", required=True, help="生成 LLM(如 gpt-5.4-mini / gemma4:31b)")
    ap.add_argument("--gen-backend", required=True, choices=["openai", "ollama"])
    ap.add_argument("--db", required=True, help="Neo4j database 名稱(會寫進 config.yaml)")
    ap.add_argument("--output-dir", required=True, help="此次執行的輸出資料夾")
    ap.add_argument("--threshold", default=None, help="固定 6b threshold;不給則 autotune")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--contract", default=CON,
                    help=f"合約名稱(預設 {CON})。決定輸入/輸出檔名({{contract}}_parsed/norms/embeddings)"
                         f"與傳給 6a/6c 的 --contract-doc;換第二份合約(如 Online39)用這個。")
    ap.add_argument("--skip-step1", action="store_true",
                    help="跳過 step1 parser,直接沿用既有 parsed(--reg-parsed/--con-parsed)")
    ap.add_argument("--raw-reg", default=str(ROOT / "inputs" / "raw" / "regulatory" / f"{REG}.txt"),
                    help="step1 的原始法規 txt(--skip-step1 時不用)")
    ap.add_argument("--raw-con", default=None,
                    help="step1 的原始合約 txt(預設 inputs/raw/contracts/<contract>.txt;--skip-step1 時不用)")
    ap.add_argument("--reg-parsed", default=str(ROOT / "inputs" / "parsed_regulatory" / f"{REG}_parsed.json"),
                    help="--skip-step1 時的已切分法規 json(不跳過時改用 step1 在 output-dir 產出的)")
    ap.add_argument("--con-parsed", default=None,
                    help="--skip-step1 時的已切分合約 json(預設 inputs/parsed_contracts/<contract>_parsed.json)")
    ap.add_argument("--gt", default=str(EVAL / "gt" / "online124_ground_truth.csv"),
                    help="autotune 用的 ground truth(固定 threshold 時不需要;換合約記得換成該合約的 GT)")
    args = ap.parse_args()

    CONTRACT = args.contract
    raw_con = args.raw_con or str(ROOT / "inputs" / "raw" / "contracts" / f"{CONTRACT}.txt")

    OUT = Path(args.output_dir).resolve()
    (OUT / "costs").mkdir(parents=True, exist_ok=True)
    CONFIG = Path(args.config).resolve()
    # step1:預設跑(parse 到 output-dir);--skip-step1 則沿用既有 parsed。
    if args.skip_step1:
        REG_PARSED = Path(args.reg_parsed).resolve()
        CON_PARSED = Path(args.con_parsed or str(ROOT / "inputs" / "parsed_contracts" / f"{CONTRACT}_parsed.json")).resolve()
    else:
        REG_PARSED = OUT / "parsed_regulatory" / f"{REG}_parsed.json"
        CON_PARSED = OUT / "parsed_contracts" / f"{CONTRACT}_parsed.json"
    GROUPS = OUT / "obligation_groups" / f"{REG}_obligation_groups.json"
    REG_NORMS = OUT / "norms" / f"{REG}_norms.json"
    CON_NORMS = OUT / "norms" / f"{CONTRACT}_norms.json"
    REG_EMB = OUT / "embeddings" / f"{REG}_embeddings.json"
    CON_EMB = OUT / "embeddings" / f"{CONTRACT}_embeddings.json"
    CR = OUT / "compliance_results"
    FAIL = OUT / "failures"

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    env["GEN_BACKEND"] = args.gen_backend
    env["GEN_MODEL"] = args.gen_model
    env["COST_DIR"] = str(OUT / "costs")

    if not CONFIG.exists():
        raise FileNotFoundError(
            f"找不到 config.yaml:{CONFIG}\n"
            f"請先複製設定再跑(PowerShell):Copy-Item config.example.yaml config.yaml\n"
            f"(並填好 neo4j 連線;.env.example 複製成 .env 填 OPENAI_API_KEY;見 README「安裝」)")
    set_db(CONFIG, args.db)

    print("=" * 70)
    print(f"(A) PIPELINE  gen={args.gen_model} backend={args.gen_backend} db={args.db}")
    print(f"    輸出: {OUT}")
    print("=" * 70)

    # Step1 parser(txt -> parsed json;預設跑,輸出到 output-dir)
    if not args.skip_step1:
        run(SRC / "1_parsers",
            ["regulatory_parser.py", "--input", Path(args.raw_reg).resolve(),
             "--output-dir", OUT / "parsed_regulatory"], env, "step1_parse_reg")
        run(SRC / "1_parsers",
            ["contract_parser.py", "--input", Path(raw_con).resolve(),
             "--output-dir", OUT / "parsed_contracts"], env, "step1_parse_con")
    else:
        print(f"[step1] 跳過,沿用既有 parsed:\n   reg={REG_PARSED}\n   con={CON_PARSED}")

    # Step2 obligation classifier(LLM)
    run(SRC / "2_obligation_classifier",
        ["regulatory_classifier.py", "--input", REG_PARSED, "--output-dir", OUT / "obligation_groups"],
        env, "step2_classifier")
    # Step3 extraction(LLM;含併入的 core_sentence 後處理)
    run(SRC / "3_extraction",
        ["extract_regulatory.py", "--input", REG_PARSED, "--groups", GROUPS,
         "--output-dir", OUT / "norms", "--failure-dir", FAIL], env, "step3_extract_reg")
    run(SRC / "3_extraction",
        ["extract_contract.py", "--input", CON_PARSED, "--groups", GROUPS,
         "--output-dir", OUT / "norms", "--failure-dir", FAIL], env, "step3_extract_con")
    # Step4 embedding(OpenAI text-embedding-3-large,固定)
    run(SRC / "4_embedding",
        ["embedding_generator.py", "--input", REG_NORMS, "--output-dir", OUT / "embeddings",
         "--failure-dir", FAIL], env, "step4_embed_reg")
    run(SRC / "4_embedding",
        ["embedding_generator.py", "--input", CON_NORMS, "--output-dir", OUT / "embeddings",
         "--failure-dir", FAIL], env, "step4_embed_con")
    # Step5 KG
    run(SRC / "5_kg_writer", ["kg_writer.py", "--input", REG_EMB, "--config", CONFIG], env, "step5_kg_reg")
    run(SRC / "5_kg_writer", ["kg_writer.py", "--input", CON_EMB, "--config", CONFIG], env, "step5_kg_con")
    # Step6a group filter(明確指定法規/合約 doc,讓 Neo4j 查詢對到正確的 Document)
    run(SRC / "6_matching",
        ["stage_a_group_filter.py", "--output-dir", CR, "--config", CONFIG,
         "--regulatory-doc", REG, "--contract-doc", CONTRACT],
        env, "step6a_group_filter")

    # 6b threshold:給了 --threshold 就固定;否則 autotune(純 code)
    if args.threshold is not None:
        threshold = str(args.threshold)
        (OUT / "threshold.txt").write_text(f"{threshold}\n", encoding="utf-8")
        print(f"[threshold] 固定採用 {threshold}(不 autotune)")
    else:
        res = subprocess.run(
            [PY, "autotune_threshold.py", "--stage-a", str(CR / "stage_a_pairs.json"),
             "--reg-emb", str(REG_EMB), "--con-emb", str(CON_EMB),
             "--gt", str(Path(args.gt).resolve()), "--out", str(OUT)],
            cwd=str(ROOT / "_archive" / "tools"), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace")
        print(res.stdout or "")
        if res.returncode != 0:
            print(res.stderr or "")
            raise RuntimeError("autotune 失敗")
        tf = OUT / "threshold.txt"
        if tf.exists():
            threshold = tf.read_text(encoding="utf-8").strip()
        else:
            m = re.search(r"CHOSEN_THRESHOLD=([0-9.]+)", res.stdout or "")
            threshold = m.group(1) if m else "0.50"
        print(f"[threshold] autotune 採用 {threshold}")

    # Step6b semantic(明確指定 --input,讀 6a 寫在 output-dir 的 stage_a_pairs.json)
    run(SRC / "6_matching",
        ["stage_b_semantic.py", "--input", CR / "stage_a_pairs.json", "--output-dir", CR,
         "--threshold", threshold, "--config", CONFIG],
        env, "step6b_semantic")
    # Step6c reasoning(LLM;結構化為主、原文為輔;明確指定法規/合約 doc)
    run(SRC / "6_matching",
        ["stage_c_reasoning.py", "--input", CR / "stage_b_pairs.json", "--output-dir", CR,
         "--failure-dir", FAIL, "--config", CONFIG,
         "--regulatory-doc", REG, "--contract-doc", CONTRACT], env, "step6c_reasoning")
    # Step7 aggregate -> compliant.csv(--reg-norms 明確指定;預設 OFF 模式不會用到,但避免落到舊 output/)
    run(SRC / "7_aggregation",
        ["aggregator.py", "--input", CR / "stage_c_results.json", "--output-dir", CR,
         "--reg-norms", REG_NORMS],
        env, "step7_aggregate")

    # 全流程 wall-clock 落地(供 run_evaluation.py 讀)
    total = sum(s for _, s in STEP_TIMES)
    step_times = {"total_s": round(total, 3), "steps": [{"label": l, "seconds": round(s, 3)} for l, s in STEP_TIMES]}
    (OUT / "costs" / "step_times.json").write_text(
        json.dumps(step_times, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"PIPELINE 完成。compliant.csv: {CR / 'compliant.csv'}")
    print(f"threshold={threshold} | 總時間 {total:.1f}s ({total/60:.1f} 分鐘)")
    print(f"成本/時間記錄: {OUT / 'costs'}")
    print("接著評估請執行:  python run_evaluation.py --output-dir", OUT, "--gen-model", args.gen_model)
    print("=" * 70)


if __name__ == "__main__":
    main()
