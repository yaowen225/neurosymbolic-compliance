"""
run_canonical.py — 最終 canonical 實驗 orchestrator(可重現、可續跑、完整存檔)。

跑:contracts × {system + 4 baselines} × n_runs。每個 system run 走「完整 pipeline」;每個 run 用
「全新且唯一的」 Neo4j KG database(kg_writer --clear 清空後寫入),記進 run_meta.json。

設計重點:
- **設定檔驅動**(--config 指向 canonical_*.yaml),mini / gemma 兩版只差設定,程式不寫死。
- **根目錄 = 本檔所在的 /release**,所有路徑都相對 /release,不依賴 /release 之上的任何檔案。
- **可續跑 / 防中斷**:每個 step 完成後寫一個 sentinel(<run>/.ckpt/<step>.done);重跑時
  已完成的 step 直接跳過(不重算、不重複計費、不覆蓋已產出資料)。硬碟上維護
  results/RUN_STATE.md(人看)+ results/.run_state.json(機器讀),compact 後重讀也看得到進度。
- **UTF-8**:stdout 設 UTF-8;即時印「合約 / 第幾次 / 方法 / step」。
- 每個 step 失敗自動重試(預設 3 次、指數退避);仍失敗則停下並在 RUN_STATE 標 error,
  下次重跑會從該 step 接著跑。

用法(從 /release 根目錄):
  python run_canonical.py --config canonical_mini.yaml
  python run_canonical.py --config canonical_gemma.yaml --kg-dbs db1,db2,db3,db4,db5,db6
"""
import os, re, sys, json, time, argparse, subprocess
from pathlib import Path
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]          # = /release(唯一根)
PY = sys.executable
SRC = ROOT / "src"
EVAL = ROOT / "evaluation"
REG = "GDPR_DPA_Requirements"

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

import yaml


def log(msg):
    print(msg, flush=True)


def gt_path(contract: str) -> Path:
    return EVAL / "gt" / f"{contract.lower()}_ground_truth.csv"


def set_db(config_path: Path, db: str):
    cfg = config_path.read_text(encoding="utf-8")
    cfg2 = re.sub(r'(database:\s*")[^"]*(")', r"\g<1>" + db + r"\g<2>", cfg, count=1)
    config_path.write_text(cfg2, encoding="utf-8")
    log(f"      [config] neo4j.database -> {db}")


# ---------------- 進度持久化 ----------------
class State:
    def __init__(self, results: Path, plan):
        self.results = results
        self.plan = plan                       # list of (contract, run_no, kg_db)
        self.json_path = results / ".run_state.json"
        self.md_path = results / "RUN_STATE.md"

    def ckpt(self, contract, run_no):
        return self.results / contract / f"run{run_no}" / ".ckpt"

    def done(self, contract, run_no, step):
        return (self.ckpt(contract, run_no) / f"{step}.done").exists()

    def mark(self, contract, run_no, step):
        d = self.ckpt(contract, run_no)
        d.mkdir(parents=True, exist_ok=True)
        (d / f"{step}.done").write_text(datetime.now().isoformat(), encoding="utf-8")

    SYS_STEPS = ["run_meta", "parse_reg", "parse_con", "classifier", "extract_reg", "extract_con",
                 "embed_reg", "embed_con", "kg", "stage_a", "stage_b", "stage_c", "aggregate",
                 "sys_eval", "sys_analysis", "sys_funnel"]
    BL_STEPS = ["bl_naive", "bl_rag", "bl_dense", "bl_passage"]

    def write(self, current=""):
        all_steps = self.SYS_STEPS + self.BL_STEPS
        state = {"updated": datetime.now().isoformat(), "current": current, "runs": []}
        lines = ["# RUN_STATE — canonical 實驗進度", "",
                 f"更新時間:{state['updated']}", f"目前:{current or '—'}", ""]
        total_done = total = 0
        for contract, run_no, kg_db in self.plan:
            steps = {s: self.done(contract, run_no, s) for s in all_steps}
            nd = sum(steps.values()); total_done += nd; total += len(all_steps)
            status = "✅ 完成" if nd == len(all_steps) else (f"🔄 {nd}/{len(all_steps)}" if nd else "⬜ 待跑")
            todo = [s for s in all_steps if not steps[s]]
            state["runs"].append({"contract": contract, "run": run_no, "kg_db": kg_db,
                                  "done": nd, "total": len(all_steps), "todo": todo})
            lines.append(f"- **{contract} / run{run_no}** (kg={kg_db}): {status}"
                         + (f" — 待:{', '.join(todo)}" if todo else ""))
        lines.insert(4, f"總進度:{total_done}/{total} steps\n")
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        self.json_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        self.md_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------- 單一 step 執行(含 sentinel 跳過 + 重試 + 計時) ----------------
def run_step(state, contract, run_no, step, cwd, argv, env, time_file, retries=3):
    if state.done(contract, run_no, step):
        log(f"      [skip] {step}(已完成)")
        return
    label = f"{contract}/run{run_no}/{step}"
    for attempt in range(1, retries + 1):
        log(f"      ▶ {step}" + (f"(第 {attempt} 次)" if attempt > 1 else ""))
        t0 = time.time()
        r = subprocess.run([PY, "-u"] + [str(a) for a in argv], cwd=str(cwd), env=env)
        dt = time.time() - t0
        if r.returncode == 0:
            _record_time(time_file, step, dt)
            state.mark(contract, run_no, step)
            log(f"      ✓ {step}  ({dt:.1f}s)")
            return
        log(f"      ✗ {step} 失敗 rc={r.returncode}(attempt {attempt}/{retries})")
        time.sleep(min(30, 5 * attempt))
    raise RuntimeError(f"step 連續失敗,停下:{label}。修正後重跑會從這裡接續。")


def _record_time(time_file: Path, step: str, dt: float):
    time_file.parent.mkdir(parents=True, exist_ok=True)
    data = {"steps": [], "total_s": 0.0}
    if time_file.exists():
        data = json.load(open(time_file, encoding="utf-8"))
    data["steps"] = [s for s in data["steps"] if s["label"] != step] + [{"label": step, "seconds": round(dt, 3)}]
    data["total_s"] = round(sum(s["seconds"] for s in data["steps"]), 3)
    json.dump(data, open(time_file, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def write_funnel(cr: Path, out_json: Path):
    sa = json.load(open(cr / "stage_a_pairs.json", encoding="utf-8"))
    sb = json.load(open(cr / "stage_b_pairs.json", encoding="utf-8"))
    sc = json.load(open(cr / "stage_c_results.json", encoding="utf-8"))
    import csv as _csv
    nrows = sum(1 for _ in _csv.reader(open(cr / "compliant.csv", encoding="utf-8-sig"))) - 1
    funnel = {
        "6a_group_filter": {"n_pairs": sa.get("n_pairs"), "n_reg_norms": sa.get("n_reg_norms"),
                            "n_contract_norms": sa.get("n_contract_norms")},
        "6b_semantic": {"threshold": sb.get("threshold"), "n_input": sb.get("n_input"),
                        "n_survivors": sb.get("n_survivors"),
                        "filtered_out": (sb.get("n_input") or 0) - (sb.get("n_survivors") or 0)},
        "6c_reasoning": {"n_judged": sc.get("n_judged"), "verdict_counts": sc.get("verdict_counts")},
        "aggregated_predictions": nrows,
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    json.dump(funnel, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def main():
    ap = argparse.ArgumentParser(description="canonical 實驗 orchestrator(可重現/可續跑)")
    ap.add_argument("--config", default="canonical_mini.yaml", help="設定檔(相對 /release)")
    ap.add_argument("--kg-dbs", default=None, help="覆寫設定檔的 KG db 清單(逗號分隔;gemma/不同電腦用)")
    args = ap.parse_args()

    cfg = yaml.safe_load(open(ROOT / args.config, encoding="utf-8"))
    contracts = cfg["contracts"]
    n_runs = int(cfg["n_runs"])
    threshold = str(cfg["threshold"])
    gen_backend = cfg["gen_backend"]
    gen_model = cfg["gen_model"]
    stage_c_workers = str(cfg.get("stage_c_workers", 1))
    results = (ROOT / cfg.get("results_dir", "results")).resolve()
    config_yaml = ROOT / cfg.get("config_yaml", "config.yaml")
    kg_dbs = (args.kg_dbs.split(",") if args.kg_dbs else cfg["kg_dbs"])

    need = len(contracts) * n_runs
    if len(kg_dbs) < need:
        raise SystemExit(f"KG db 不足:需要 {need} 個(每 run 一個不重複),只給了 {len(kg_dbs)} 個。")

    # 計畫:每 (contract, run) 配一個唯一 db
    plan = []
    for ci, contract in enumerate(contracts):
        for ri in range(n_runs):
            plan.append((contract, ri + 1, kg_dbs[ci * n_runs + ri]))

    state = State(results, plan)
    state.write(current="啟動")
    log("=" * 72)
    log(f"CANONICAL 實驗  backend={gen_backend} model={gen_model} threshold={threshold} "
        f"6c_workers={stage_c_workers}")
    log(f"  contracts={contracts} × runs={n_runs};結果 -> {results}")
    log(f"  KG dbs(每 run 唯一):{kg_dbs[:need]}")
    log("=" * 72)

    env_base = dict(os.environ)
    env_base.update(PYTHONUNBUFFERED="1", PYTHONUTF8="1", PYTHONIOENCODING="utf-8",
                    GEN_BACKEND=gen_backend, GEN_MODEL=gen_model)

    for contract, run_no, kg_db in plan:
        gt = gt_path(contract)
        run_dir = results / contract / f"run{run_no}"
        sysd = run_dir / "system"
        P = {k: sysd / k for k in ["parsed", "norms", "obligation_groups", "embeddings",
                                   "compliance_results", "analysis", "eval", "cost", "time", "failures"]}
        CR = P["compliance_results"]
        reg_parsed = P["parsed"] / f"{REG}_parsed.json"
        con_parsed = P["parsed"] / f"{contract}_parsed.json"
        groups = P["obligation_groups"] / f"{REG}_obligation_groups.json"
        reg_norms = P["norms"] / f"{REG}_norms.json"
        con_norms = P["norms"] / f"{contract}_norms.json"
        reg_emb = P["embeddings"] / f"{REG}_embeddings.json"
        con_emb = P["embeddings"] / f"{contract}_embeddings.json"
        time_file = P["time"] / "step_times.json"
        env = dict(env_base); env["COST_DIR"] = str(P["cost"])

        banner = f"===== {contract} / run{run_no}  (KG db={kg_db}) ====="
        log("\n" + banner)
        state.write(current=banner)

        def step(name, cwd, argv, e=env):
            run_step(state, contract, run_no, name, cwd, argv, e, time_file)
            state.write(current=f"{contract}/run{run_no}/{name}")

        # --- run_meta ---
        if not state.done(contract, run_no, "run_meta"):
            run_dir.mkdir(parents=True, exist_ok=True)
            json.dump({"contract": contract, "run": run_no, "gen_backend": gen_backend,
                       "gen_model": gen_model, "embedding_model": cfg.get("embedding_model", "text-embedding-3-large"),
                       "temperature": cfg.get("temperature", 0), "threshold": threshold, "kg_db": kg_db,
                       "stage_c_workers": int(stage_c_workers), "timestamp": datetime.now().isoformat()},
                      open(run_dir / "run_meta.json", "w", encoding="utf-8"), ensure_ascii=False, indent=2)
            state.mark(contract, run_no, "run_meta"); state.write()

        raw_reg = ROOT / "inputs" / "raw" / "regulatory" / f"{REG}.txt"
        raw_con = ROOT / "inputs" / "raw" / "contracts" / f"{contract}.txt"

        # --- system pipeline ---
        step("parse_reg", SRC / "1_parsers", ["regulatory_parser.py", "--input", raw_reg, "--output-dir", P["parsed"]])
        step("parse_con", SRC / "1_parsers", ["contract_parser.py", "--input", raw_con, "--output-dir", P["parsed"]])
        step("classifier", SRC / "2_obligation_classifier",
             ["regulatory_classifier.py", "--input", reg_parsed, "--output-dir", P["obligation_groups"]])
        step("extract_reg", SRC / "3_extraction",
             ["extract_regulatory.py", "--input", reg_parsed, "--groups", groups,
              "--output-dir", P["norms"], "--failure-dir", P["failures"]])
        step("extract_con", SRC / "3_extraction",
             ["extract_contract.py", "--input", con_parsed, "--groups", groups,
              "--output-dir", P["norms"], "--failure-dir", P["failures"]])
        step("embed_reg", SRC / "4_embedding",
             ["embedding_generator.py", "--input", reg_norms, "--output-dir", P["embeddings"], "--failure-dir", P["failures"]])
        step("embed_con", SRC / "4_embedding",
             ["embedding_generator.py", "--input", con_norms, "--output-dir", P["embeddings"], "--failure-dir", P["failures"]])

        # KG:清空該 run 專屬 db 後寫入(kg_reg --clear + kg_con)。兩個子呼叫合成一個 sentinel。
        if not state.done(contract, run_no, "kg"):
            set_db(config_yaml, kg_db)
            run_step(state, contract, run_no, "kg__reg", SRC / "5_kg_writer",
                     ["kg_writer.py", "--input", reg_emb, "--config", config_yaml, "--clear"], env, time_file)
            run_step(state, contract, run_no, "kg__con", SRC / "5_kg_writer",
                     ["kg_writer.py", "--input", con_emb, "--config", config_yaml], env, time_file)
            state.mark(contract, run_no, "kg"); state.write(current=f"{contract}/run{run_no}/kg")
        else:
            log("      [skip] kg(已完成)")
        set_db(config_yaml, kg_db)   # 確保 6a/6b/6c 對到本 run 的 db(續跑時也對)

        step("stage_a", SRC / "6_matching",
             ["stage_a_group_filter.py", "--output-dir", CR, "--config", config_yaml,
              "--regulatory-doc", REG, "--contract-doc", contract])
        (sysd / "threshold.txt").parent.mkdir(parents=True, exist_ok=True)
        (sysd / "threshold.txt").write_text(f"{threshold}\n", encoding="utf-8")
        step("stage_b", SRC / "6_matching",
             ["stage_b_semantic.py", "--input", CR / "stage_a_pairs.json", "--output-dir", CR,
              "--threshold", threshold, "--config", config_yaml])
        env_6c = dict(env); env_6c["STAGE_C_WORKERS"] = stage_c_workers
        step("stage_c", SRC / "6_matching",
             ["stage_c_reasoning.py", "--input", CR / "stage_b_pairs.json", "--output-dir", CR,
              "--failure-dir", P["failures"], "--config", config_yaml,
              "--regulatory-doc", REG, "--contract-doc", contract], e=env_6c)
        step("aggregate", SRC / "7_aggregation",
             ["aggregator.py", "--input", CR / "stage_c_results.json", "--output-dir", CR, "--reg-norms", reg_norms])

        # --- system eval / analysis / funnel ---
        step("sys_eval", EVAL,
             ["evaluate_retrieval.py", "--predictions", CR / "compliant.csv",
              "--ground-truth", gt, "--output", P["eval"] / "evaluation_results_system.csv"])
        step("sys_analysis", ROOT,
             [Path(__file__).resolve().parent / "run_analysis.py", "--output-dir", sysd, "--contract", contract, "--gt", gt])
        if not state.done(contract, run_no, "sys_funnel"):
            write_funnel(CR, P["analysis"] / "funnel.json")
            state.mark(contract, run_no, "sys_funnel"); state.write()

        # --- baselines ---
        bl_root = run_dir / "baselines"
        def baseline(name, sentinel, argv_builder, needs_threshold=False):
            if state.done(contract, run_no, sentinel):
                log(f"      [skip] {sentinel}(已完成)")
                return
            bld = bl_root / f"{name}_baseline"
            (bld / "cost").mkdir(parents=True, exist_ok=True)
            (bld / "eval").mkdir(parents=True, exist_ok=True)
            csv_out = bld / f"{name}_compliant.csv"
            be = dict(env_base); be["COST_DIR"] = str(bld / "cost")
            argv = argv_builder(csv_out)
            if needs_threshold:
                argv += ["--threshold", threshold]
            tf = bld / "time" / "time.json"
            run_step(state, contract, run_no, sentinel + "_pred", EVAL, argv, be, tf)
            ev = bld / "eval" / "evaluation_results.csv"
            run_step(state, contract, run_no, sentinel + "_eval", EVAL,
                     ["evaluate_retrieval.py", "--predictions", csv_out, "--ground-truth", gt, "--output", ev], be, tf)
            state.mark(contract, run_no, sentinel)     # 兩個子步都成功才標 counted sentinel
            state.write(current=f"{contract}/run{run_no}/{sentinel}")

        baseline("naive", "bl_naive",
                 lambda o: ["baseline_naiveLLM.py", "--reg-parsed", reg_parsed, "--con-parsed", con_parsed, "--out", o])
        baseline("rag", "bl_rag",
                 lambda o: ["baseline_Traditional_RAG.py", "--reg-parsed", reg_parsed, "--con-parsed", con_parsed, "--out", o],
                 needs_threshold=True)
        baseline("dense", "bl_dense",
                 lambda o: ["baseline_dense_retrieval.py", "--reg-parsed", reg_parsed, "--con-parsed", con_parsed, "--out", o],
                 needs_threshold=True)
        baseline("passage", "bl_passage",
                 lambda o: ["baseline_passage_classification.py", "--con-parsed", con_parsed, "--out", o])

        log(f"  ✅ {contract}/run{run_no} 完成")
        state.write(current=f"{contract}/run{run_no} 完成")

    state.write(current="全部完成")
    log("\n" + "=" * 72)
    log("CANONICAL 實驗全部完成。接著請跑:python runners/build_final_report.py")
    log("=" * 72)


if __name__ == "__main__":
    main()
