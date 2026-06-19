"""
build_ablation_report.py — 算「四變體 × 兩份合約」的 6c-only ablation 對照表(mean±std)。

main 用 canonical 現成 n=3 結果(results/<c>/run<N>/system/eval/);三個 ablation 變體用
results_ablation/<variant>/<c>/run<N>/eval/。同一支 containment 評估器、同 threshold 0.40、temp 0。
輸出 results_ablation/ABLATION_REPORT.md。

用法:python runners/build_ablation_report.py
"""
import sys, csv, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)

CONTRACTS = ["Online124", "Online39"]
RUNS = [1, 2, 3]
# 顯示順序:main(對照)在最前
VARIANTS = [
    ("main", "結構化為主 + 原文為輔(= 現行 canonical 6c)"),
    ("structured_only", "只給結構化欄位(不給原文)"),
    ("text_only", "只給原文(不給結構化欄位)"),
    ("hybrid_textprimary", "兩者都給,但原文為主、結構化為輔"),
]


def eval_path(variant, c, r):
    if variant == "main":
        return ROOT / "results" / c / f"run{r}" / "system" / "eval" / "evaluation_results_system.csv"
    return ROOT / "results_ablation" / variant / c / f"run{r}" / "eval" / "evaluation_results.csv"


def read_eval(p):
    if not p.exists():
        return None
    d = {}
    for row in csv.reader(open(p, encoding="utf-8-sig")):
        if len(row) == 2:
            d[row[0]] = row[1]
    def fl(k):
        try:
            return float(d.get(k, ""))
        except ValueError:
            return None
    return {"P": fl("Precision"), "R": fl("Recall"), "F1": fl("F1-Score"),
            "TP": fl("True Positives (TP)"), "FP": fl("False Positives (FP)"), "FN": fl("False Negatives (FN)")}


def agg(vals):
    xs = [v for v in vals if v is not None]
    if not xs:
        return None
    m = statistics.mean(xs)
    s = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return (m, s, min(xs), max(xs))


def cell(a, nd=3):
    if a is None:
        return "—"
    m, s, lo, hi = a
    return f"**{m:.{nd}f}** ±{s:.{nd}f} [{lo:.{nd}f}–{hi:.{nd}f}]"


def main():
    L = []
    def w(s=""): L.append(s)
    w("# 6c-only Ablation — 四變體 × 兩份合約(mean±std,n=3)")
    w("")
    w("控制變因:**只換 6c 的輸入型態 / 判斷主從**,其餘前置流程(parse→…→6b)直接重用 canonical 已跑好的資料,"
      "四變體**共用同一段實質判準**(對齊現行 main 6c)。threshold 0.40、temp 0、同一支 containment 評估器。")
    w("")
    w("變體:")
    for name, desc in VARIANTS:
        w(f"- **{name}** — {desc}")
    w("")
    w("每格 = **mean** ±std [min–max](n=3)。")
    w("")

    # collect
    data = {v[0]: {c: {k: [] for k in ["P", "R", "F1", "TP", "FP", "FN"]} for c in CONTRACTS} for v in VARIANTS}
    evd = {v[0]: {c: {} for c in CONTRACTS} for v in VARIANTS}   # run-indexed,給合算配對用
    missing = []
    for name, _ in VARIANTS:
        for c in CONTRACTS:
            for r in RUNS:
                e = read_eval(eval_path(name, c, r))
                if e is None:
                    missing.append(f"{name}/{c}/run{r}")
                    continue
                evd[name][c][r] = e
                for k in data[name][c]:
                    data[name][c][k].append(e[k])

    for c in CONTRACTS:
        w(f"## {c}")
        w("")
        w("| 變體 | P | R | F1 | TP | FP | FN |")
        w("|---|---|---|---|---|---|---|")
        for name, _ in VARIANTS:
            g = lambda k, nd=3: cell(agg(data[name][c][k]), nd)
            w(f"| {name} | {g('P')} | {g('R')} | {g('F1')} | {g('TP',1)} | {g('FP',1)} | {g('FN',1)} |")
        w("")

    # ===== 兩份合算(macro / micro),同 FINAL_REPORT §1 的算法 =====
    def prf(tp, fp, fn):
        p = tp / (tp + fp) if (tp + fp) else 0.0
        r = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * p * r / (p + r) if (p + r) else 0.0
        return p, r, f1
    comb = {v[0]: {k: [] for k in ["macroP", "macroR", "macroF1", "microP", "microR", "microF1", "TP", "FP", "FN"]} for v in VARIANTS}
    for name, _ in VARIANTS:
        for r in RUNS:
            es = [evd[name][c].get(r) for c in CONTRACTS]
            if any(e is None for e in es):
                continue
            tp = sum(e["TP"] for e in es); fp = sum(e["FP"] for e in es); fn = sum(e["FN"] for e in es)
            mp, mr, mf = prf(tp, fp, fn)
            comb[name]["microP"].append(mp); comb[name]["microR"].append(mr); comb[name]["microF1"].append(mf)
            comb[name]["macroP"].append(statistics.mean([e["P"] for e in es]))
            comb[name]["macroR"].append(statistics.mean([e["R"] for e in es]))
            comb[name]["macroF1"].append(statistics.mean([e["F1"] for e in es]))
            comb[name]["TP"].append(tp); comb[name]["FP"].append(fp); comb[name]["FN"].append(fn)

    w("## 兩份合算(Online124 + Online39;n=3)")
    w("")
    w("每個 run i 把兩份同 index 配一組(共 3 組)。**micro** = 兩份 TP/FP/FN 合計後算 P=TP/(TP+FP)、"
      "R=TP/(TP+FN)、F1;**macro** = 兩份各自 P/R/F1 再平均。TP/FP/FN 為兩份合計。每格 **mean** ±std [min–max]。")
    w("")
    w("### Macro 平均(兩份各自算 P/R/F1 再平均)")
    w("")
    w("| 變體 | macro P | macro R | macro F1 | TP | FP | FN |")
    w("|---|---|---|---|---|---|---|")
    for name, _ in VARIANTS:
        d = comb[name]
        w(f"| {name} | {cell(agg(d['macroP']))} | {cell(agg(d['macroR']))} | {cell(agg(d['macroF1']))} | "
          f"{cell(agg(d['TP']),1)} | {cell(agg(d['FP']),1)} | {cell(agg(d['FN']),1)} |")
    w("")
    w("### Micro 合計(兩份 TP/FP/FN 合計後再算 P/R/F1)")
    w("")
    w("| 變體 | micro P | micro R | micro F1 | TP | FP | FN |")
    w("|---|---|---|---|---|---|---|")
    for name, _ in VARIANTS:
        d = comb[name]
        w(f"| {name} | {cell(agg(d['microP']))} | {cell(agg(d['microR']))} | {cell(agg(d['microF1']))} | "
          f"{cell(agg(d['TP']),1)} | {cell(agg(d['FP']),1)} | {cell(agg(d['FN']),1)} |")
    w("")

    w("## 來源")
    w("")
    w("- main:`results/<contract>/run{1,2,3}/system/eval/evaluation_results_system.csv`(canonical 現成)。")
    w("- structured_only / text_only / hybrid_textprimary:"
      "`results_ablation/<variant>/<contract>/run{1,2,3}/eval/evaluation_results.csv`。")
    w("- prompt:`src_ablation/ablation_prompts.py`(共用判準段由現行 main 6c 即時擷取,故自動對齊)。")
    if missing:
        w("")
        w(f"> 注意:以下單位尚無 eval 結果(可能 ablation 還沒跑完):{', '.join(missing)}")
    w("")

    out = ROOT / "results_ablation" / "ABLATION_REPORT.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[ablation-report] 已寫 {out}" + (f"(缺 {len(missing)} 個單位)" if missing else ""))


if __name__ == "__main__":
    main()
