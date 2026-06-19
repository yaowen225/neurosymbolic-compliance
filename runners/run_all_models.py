"""
run_all_models.py — 批次 wrapper(薄層,無重複邏輯)

依序對 MODELS 清單裡的每個模型呼叫 run_pipeline.py(+ 選擇性 run_evaluation.py)。
本檔不含任何 pipeline / 評估邏輯,只是把參數轉給那兩支 runner。要改流程請改 runner,不要改這裡。

可關掉視窗/SSH 仍續跑(detached)範例見 README。進度寫到 <output-base>/PROGRESS.log。

設定:直接改下方 MODELS 區塊(每個模型的 gen-model / backend / db / threshold)。
評估要跑哪些 target 用 --eval-target 控制(透傳給 run_evaluation,可複選;預設 system naive)。

用法:
  python run_all_models.py                         # pipeline + 評估(system naive)
  python run_all_models.py --eval-target all       # 評估 system naive rag
  python run_all_models.py --eval-target system    # 只評估主系統
  python run_all_models.py --no-eval               # 只跑 pipeline
"""

import os
import sys
import time
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PY = sys.executable

# ==================== 設定區塊(改這裡) ====================
# threshold 給值(字串或數字皆可)=固定;None=autotune。Neo4j 各 DB 請先自行清空。
MODELS = [
    {"model_dir": "gpt-5.4-mini", "gen_model": "gpt-5.4-mini", "backend": "openai", "db": "exp1", "threshold": "0.45"},
    {"model_dir": "gpt-5.4-nano", "gen_model": "gpt-5.4-nano", "backend": "openai", "db": "exp2", "threshold": "0.45"},
    # local 模型範例(在實驗室電腦、config.yaml 設好 ollama 後):
    # {"model_dir": "gemma4-31b", "gen_model": "gemma4:31b", "backend": "ollama", "db": "exp3", "threshold": None},
]
# =============================================================


def main():
    ap = argparse.ArgumentParser(description="批次跑多個模型(pipeline + 評估)")
    ap.add_argument("--output-base", default="runs", help="輸出根資料夾(各模型放其下 model_dir)")
    ap.add_argument("--no-eval", action="store_true", help="只跑 pipeline,不跑評估")
    ap.add_argument("--eval-target", nargs="+", default=["system", "naive"],
                    choices=["system", "naive", "rag", "dense", "passage", "all"], metavar="TARGET",
                    help="評估哪些 target(透傳給 run_evaluation,可複選):system/naive/rag/dense/passage/all;"
                         "預設 'system naive'(原本批次行為)。")
    args = ap.parse_args()

    base = ROOT / args.output_base
    log = base / "PROGRESS.log"
    base.mkdir(parents=True, exist_ok=True)

    def logmsg(msg):
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"

    logmsg("=" * 70)
    logmsg(f"run_all_models START (pid={os.getpid()}) — {[m['gen_model'] for m in MODELS]}")
    for m in MODELS:
        out = base / m["model_dir"]
        logmsg("-" * 70)
        logmsg(f"MODEL START: {m['gen_model']} ({m['backend']}) db={m['db']} thr={m['threshold'] or 'autotune'}")
        cmd = [PY, "-u", str(Path(__file__).resolve().parent / "run_pipeline.py"),
               "--gen-model", m["gen_model"], "--gen-backend", m["backend"],
               "--db", m["db"], "--output-dir", str(out)]
        if m["threshold"] is not None:
            cmd += ["--threshold", str(m["threshold"])]   # str():填數字也不會讓 subprocess crash
        t0 = time.time()
        with open(log, "a", encoding="utf-8") as lf:
            r = subprocess.run(cmd, cwd=str(ROOT), env=env, stdout=lf, stderr=lf)
        if r.returncode != 0:
            logmsg(f"MODEL PIPELINE FAILED: {m['gen_model']} rc={r.returncode}")
            continue
        if not args.no_eval:
            ecmd = [PY, "-u", str(Path(__file__).resolve().parent / "run_evaluation.py"), "--output-dir", str(out),
                    "--gen-model", m["gen_model"], "--gen-backend", m["backend"],
                    "--target", *args.eval_target]
            with open(log, "a", encoding="utf-8") as lf:
                er = subprocess.run(ecmd, cwd=str(ROOT), env=env, stdout=lf, stderr=lf)
            if er.returncode != 0:   # 評估失敗也要記,不可當成功
                logmsg(f"MODEL EVAL FAILED: {m['gen_model']} rc={er.returncode}")
                continue
        logmsg(f"MODEL DONE: {m['gen_model']} | 耗時 {(time.time()-t0)/60:.1f} 分鐘")
    logmsg("run_all_models ALL DONE")


if __name__ == "__main__":
    main()
