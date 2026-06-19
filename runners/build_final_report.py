"""
build_final_report.py — 讀 results/ 各 run 的原始輸出,**計算統計**寫成 results/FINAL_REPORT.md。

呈現原則(全報告一致):
- 每格 = **mean**(粗體、最顯眼) ±std [min–max];std 用樣本標準差,小數固定 3 位。
- "n=3" 只寫在每張表標題一次。只列數據,不寫結論/分析。

版面:
  §1 結論總表(兩份合算;macro 一表、micro 一表):方法 × {P,R,F1,TP,FP,FN,$,time}
  §2 兩份分開的效能表(各一張)
  §3 結論 Funnel(兩份合算;主系統;每關 輸入/通過/篩除/篩除占比)
  §4 兩份分開的 Funnel(各一張)
  §5 附錄:每次 run 的效能全展開
  §6 附錄:每次 run 的 Funnel 全展開
  §7 Provenance(含 macro/micro/combined 計算方式)

用法:python build_final_report.py            # 預設 results/ + canonical_mini.yaml
      python build_final_report.py --results results_gemma --config canonical_gemma.yaml
"""
import os, sys, csv, json, glob, argparse, statistics
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
import yaml

METHODS = ["system", "naive", "rag", "dense", "passage"]
ND = 3


# ---------------- 讀取 ----------------
def read_eval(p: Path):
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
    return {"TP": fl("True Positives (TP)"), "FP": fl("False Positives (FP)"), "FN": fl("False Negatives (FN)"),
            "P": fl("Precision"), "R": fl("Recall"), "F1": fl("F1-Score")}


def usage_usd(usage_path: Path, gen_model, pricing):
    if not usage_path.exists():
        return None
    data = json.load(open(usage_path, encoding="utf-8"))
    unc = cac = out = emb = 0
    for _step, e in data.items():
        c = e.get("chat", {}) or {}; em = e.get("embedding", {}) or {}
        unc += c.get("uncached_input", 0); cac += c.get("cached_input", 0)
        out += c.get("output", 0); emb += em.get("input", 0)
    cp = pricing.get(gen_model); ep = pricing.get("text-embedding-3-large", {}).get("input", 0)
    if not cp:
        return (emb * ep) / 1e6
    return (unc * cp["input"] + cac * cp["cached_input"] + out * cp["output"] + emb * ep) / 1e6


def total_time(time_path: Path):
    if not time_path.exists():
        return None
    return json.load(open(time_path, encoding="utf-8")).get("total_s")


# ---------------- 統計格式 ----------------
def agg(vals):
    xs = [v for v in vals if v is not None]
    if not xs:
        return None
    mean = statistics.mean(xs)
    std = statistics.stdev(xs) if len(xs) > 1 else 0.0
    return (mean, std, min(xs), max(xs), len(xs))


def cell(a):                       # **mean** ±std [min–max]
    if a is None:
        return "—"
    m, s, lo, hi, _n = a
    return f"**{m:.{ND}f}** ±{s:.{ND}f} [{lo:.{ND}f}–{hi:.{ND}f}]"


def cell_mr(a):                    # **mean** [min–max]
    if a is None:
        return "—"
    m, _s, lo, hi, _n = a
    return f"**{m:.{ND}f}** [{lo:.{ND}f}–{hi:.{ND}f}]"


def prf(tp, fp, fn):
    p = tp / (tp + fp) if (tp + fp) else 0.0
    r = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * p * r / (p + r) if (p + r) else 0.0
    return p, r, f1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results")
    ap.add_argument("--config", default="canonical_mini.yaml")
    a = ap.parse_args()
    RES = (ROOT / a.results).resolve()
    cfg = yaml.safe_load(open(ROOT / a.config, encoding="utf-8"))
    pricing = yaml.safe_load(open(ROOT / cfg.get("config_yaml", "config.yaml"), encoding="utf-8"))["pricing"]
    gm = cfg["gen_model"]
    contracts = cfg["contracts"]; n_runs = int(cfg["n_runs"])
    runs = list(range(1, n_runs + 1))

    def rd(c, r): return RES / c / f"run{r}"
    def eval_p(c, r, m):
        return (rd(c, r) / "system/eval/evaluation_results_system.csv" if m == "system"
                else rd(c, r) / "baselines" / f"{m}_baseline" / "eval" / "evaluation_results.csv")
    def cost_p(c, r, m):
        d = rd(c, r) / "system/cost" if m == "system" else rd(c, r) / "baselines" / f"{m}_baseline" / "cost"
        g = glob.glob(str(d / "*_usage.json")); return Path(g[0]) if g else d / "none.json"
    def time_p(c, r, m):
        return (rd(c, r) / "system/time/step_times.json" if m == "system"
                else rd(c, r) / "baselines" / f"{m}_baseline" / "time" / "time.json")

    # data[c][m][r] = {TP,FP,FN,P,R,F1,usd,time}
    data = {c: {m: {} for m in METHODS} for c in contracts}
    for c in contracts:
        for m in METHODS:
            for r in runs:
                e = read_eval(eval_p(c, r, m)) or {}
                e = dict(e)
                e["usd"] = usage_usd(cost_p(c, r, m), gm, pricing)
                e["time"] = total_time(time_p(c, r, m))
                data[c][m][r] = e

    # funnel[c][r] = per-stage dict
    funnel = {c: {} for c in contracts}
    for c in contracts:
        for r in runs:
            fp = rd(c, r) / "system/analysis/funnel.json"
            if not fp.exists():
                funnel[c][r] = None; continue
            f = json.load(open(fp, encoding="utf-8"))
            a6 = f["6a_group_filter"]; b6 = f["6b_semantic"]; v = f["6c_reasoning"]["verdict_counts"] or {}
            in6a = (a6.get("n_reg_norms") or 0) * (a6.get("n_contract_norms") or 0)
            pass6a = a6.get("n_pairs") or 0
            in6b = b6.get("n_input") or 0; pass6b = b6.get("n_survivors") or 0
            in6c = f["6c_reasoning"].get("n_judged") or 0; pass6c = v.get("Compliant", 0)
            funnel[c][r] = {
                "6a": {"in": in6a, "pass": pass6a, "filt": in6a - pass6a},
                "6b": {"in": in6b, "pass": pass6b, "filt": in6b - pass6b},
                "6c": {"in": in6c, "pass": pass6c, "filt": in6c - pass6c},
            }

    L = []
    def w(s=""): L.append(s)

    w("# FINAL REPORT — canonical 實驗統計")
    w("")
    w(f"- 設定:backend={cfg['gen_backend']} / model={gm} / threshold={cfg['threshold']} / "
      f"temp={cfg.get('temperature',0)} / runs={n_runs}(每 run 用獨立 KG db,見 run_meta.json)")
    w(f"- 每格 = **mean** ±std [min–max](樣本標準差,小數 3 位);只列數據,無結論/分析。")
    w(f"- 金額 = token 用量 × `config.yaml` pricing(USD/1M):{gm}=(in {pricing.get(gm,{}).get('input','?')}, "
      f"cached {pricing.get(gm,{}).get('cached_input','?')}, out {pricing.get(gm,{}).get('output','?')}), "
      f"embed-3-large in {pricing.get('text-embedding-3-large',{}).get('input','?')}")
    w("- **6c-only ablation**(只換 6c 輸入型態:structured_only / text_only / hybrid_textprimary,"
      "vs main)見 `results_ablation/ABLATION_REPORT.md`。")
    w("")

    # ============ §1 結論總表(兩份合算)============
    # 每個 run i:把兩份同 index 配一組。micro=TP/FP/FN 合計後算 P/R/F1;macro=兩份 P/R/F1 平均;$/time 合計。
    comb = {m: {"macroP": [], "macroR": [], "macroF1": [], "microP": [], "microR": [], "microF1": [],
                "TP": [], "FP": [], "FN": [], "usd": [], "time": []} for m in METHODS}
    for m in METHODS:
        for r in runs:
            recs = [data[c][m][r] for c in contracts]
            if any(x.get("TP") is None for x in recs):
                continue
            tp = sum(x["TP"] for x in recs); fpp = sum(x["FP"] for x in recs); fn = sum(x["FN"] for x in recs)
            mp, mr, mf = prf(tp, fpp, fn)
            comb[m]["microP"].append(mp); comb[m]["microR"].append(mr); comb[m]["microF1"].append(mf)
            comb[m]["macroP"].append(statistics.mean([x["P"] for x in recs]))
            comb[m]["macroR"].append(statistics.mean([x["R"] for x in recs]))
            comb[m]["macroF1"].append(statistics.mean([x["F1"] for x in recs]))
            comb[m]["TP"].append(tp); comb[m]["FP"].append(fpp); comb[m]["FN"].append(fn)
            us = [x["usd"] for x in recs]; tm = [x["time"] for x in recs]
            comb[m]["usd"].append(sum(us) if all(u is not None for u in us) else None)
            comb[m]["time"].append(sum(tm) if all(t is not None for t in tm) else None)

    w(f"## §1 結論總表(兩份合算,Online124 + Online39;n={n_runs})")
    w("")
    w("### §1a Macro 平均(兩份各自算 P/R/F1 再平均)")
    w("")
    w("| 方法 | macro P | macro R | macro F1 | TP | FP | FN | 成本($) | 時間(s) |")
    w("|---|---|---|---|---|---|---|---|---|")
    for m in METHODS:
        d = comb[m]
        w(f"| {m} | {cell(agg(d['macroP']))} | {cell(agg(d['macroR']))} | {cell(agg(d['macroF1']))} | "
          f"{cell(agg(d['TP']))} | {cell(agg(d['FP']))} | {cell(agg(d['FN']))} | "
          f"{cell(agg(d['usd']))} | {cell(agg(d['time']))} |")
    w("")
    w("### §1b Micro 合計(兩份 TP/FP/FN 合計後再算 P/R/F1)")
    w("")
    w("| 方法 | micro P | micro R | micro F1 | TP | FP | FN | 成本($) | 時間(s) |")
    w("|---|---|---|---|---|---|---|---|---|")
    for m in METHODS:
        d = comb[m]
        w(f"| {m} | {cell(agg(d['microP']))} | {cell(agg(d['microR']))} | {cell(agg(d['microF1']))} | "
          f"{cell(agg(d['TP']))} | {cell(agg(d['FP']))} | {cell(agg(d['FN']))} | "
          f"{cell(agg(d['usd']))} | {cell(agg(d['time']))} |")
    w("")
    w("> TP/FP/FN、成本、時間為兩份合計(micro 口徑);macro/micro 僅差在 P/R/F1 的彙整方式。")
    w("")

    # ============ §2 兩份分開的效能表 ============
    for c in contracts:
        w(f"## §2 {c} — 效能(n={n_runs})")
        w("")
        w("| 方法 | P | R | F1 | TP | FP | FN | 成本($) | 時間(s) |")
        w("|---|---|---|---|---|---|---|---|---|")
        for m in METHODS:
            g = lambda k: agg([data[c][m][r].get(k) for r in runs])
            w(f"| {m} | {cell(g('P'))} | {cell(g('R'))} | {cell(g('F1'))} | "
              f"{cell(g('TP'))} | {cell(g('FP'))} | {cell(g('FN'))} | {cell(g('usd'))} | {cell(g('time'))} |")
        w("")

    # ============ §3 結論 Funnel(兩份合算,主系統)============
    def funnel_table(per_run_stage):
        """per_run_stage[stage][run] = (in,pass,filt). 兩個篩除率欄:本關篩除率=篩除/該關輸入;
        占初始總配對=篩除/該 run 的 6a 輸入。皆 mean[min–max]。"""
        w("| 階段 | 輸入配對 | 通過 | 篩除 | 本關篩除率 | 占初始總配對 |")
        w("|---|---|---|---|---|---|")
        labels = {"6a": "6a 群組過濾(reg×con 全配對 → 交集非空)",
                  "6b": "6b cosine≥0.40(=6a 通過 → survivors)",
                  "6c": "6c 推理(=6b 通過 → Compliant;篩除=Violation+Gap)"}
        for st in ["6a", "6b", "6c"]:
            rr = [r for r in runs if per_run_stage[st].get(r)]
            ins = [per_run_stage[st][r][0] for r in rr]
            pas = [per_run_stage[st][r][1] for r in rr]
            fil = [per_run_stage[st][r][2] for r in rr]
            pct_stage = [100.0 * per_run_stage[st][r][2] / per_run_stage[st][r][0] for r in rr if per_run_stage[st][r][0]]
            pct_init = [100.0 * per_run_stage[st][r][2] / per_run_stage["6a"][r][0]
                        for r in rr if per_run_stage["6a"].get(r) and per_run_stage["6a"][r][0]]
            w(f"| {labels[st]} | {cell_mr(agg(ins))} | {cell_mr(agg(pas))} | {cell_mr(agg(fil))} | "
              f"{cell_mr(agg(pct_stage))}% | {cell_mr(agg(pct_init))}% |")
        w("")

    comb_stage = {st: {} for st in ["6a", "6b", "6c"]}
    for r in runs:
        if any(funnel[c][r] is None for c in contracts):
            continue
        for st in ["6a", "6b", "6c"]:
            i = sum(funnel[c][r][st]["in"] for c in contracts)
            p = sum(funnel[c][r][st]["pass"] for c in contracts)
            comb_stage[st][r] = (i, p, i - p)
    w(f"## §3 結論 Funnel(兩份合算,主系統;n={n_runs})")
    w("")
    w("每關各砍掉多少配對(凸顯 6a/6b/6c 三段篩選都實際在作用;不列聚合預測):")
    w("")
    funnel_table(comb_stage)

    # ============ §4 兩份分開的 Funnel ============
    for c in contracts:
        per = {st: {r: (funnel[c][r][st]["in"], funnel[c][r][st]["pass"], funnel[c][r][st]["filt"])
                    for r in runs if funnel[c][r]} for st in ["6a", "6b", "6c"]}
        w(f"## §4 {c} — Funnel(主系統;n={n_runs})")
        w("")
        funnel_table(per)

    # ============ §5 附錄:每次 run 的效能全展開 ============
    w("## §5 附錄 — 每次 run 的效能全展開")
    w("")
    for c in contracts:
        w(f"### {c}")
        w("")
        w("| 方法 | run | P | R | F1 | TP | FP | FN | $ | time(s) |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for m in METHODS:
            for r in runs:
                e = data[c][m][r]
                def s(k, nd=ND):
                    v = e.get(k)
                    return f"{v:.{nd}f}" if isinstance(v, (int, float)) else "—"
                w(f"| {m} | run{r} | {s('P')} | {s('R')} | {s('F1')} | {s('TP',0)} | {s('FP',0)} | "
                  f"{s('FN',0)} | {s('usd',6)} | {s('time',1)} |")
        w("")

    # ============ §6 附錄:每次 run 的 Funnel 全展開 ============
    w("## §6 附錄 — 每次 run 的 Funnel 全展開(主系統)")
    w("")
    for c in contracts:
        w(f"### {c}")
        w("")
        w("| run | 6a 輸入 | 6a 通過 | 6a 篩除(本關%/占初始%) | 6b 輸入 | 6b 通過 | 6b 篩除(本關%/占初始%) | 6c 輸入 | 6c Compliant | 6c 篩除(本關%/占初始%) |")
        w("|---|---|---|---|---|---|---|---|---|---|")
        for r in runs:
            f = funnel[c][r]
            if not f:
                w(f"| run{r} | — | — | — | — | — | — | — | — | — |"); continue
            init = f['6a']['in']
            def fc(st):
                d = f[st]
                ps = 100.0 * d['filt'] / d['in'] if d['in'] else 0.0
                pi = 100.0 * d['filt'] / init if init else 0.0
                return f"{d['filt']} ({ps:.1f}%/{pi:.1f}%)"
            w(f"| run{r} | {f['6a']['in']} | {f['6a']['pass']} | {fc('6a')} | "
              f"{f['6b']['in']} | {f['6b']['pass']} | {fc('6b')} | "
              f"{f['6c']['in']} | {f['6c']['pass']} | {fc('6c')} |")
        w("")

    # ============ §7 Provenance ============
    w("## §7 Provenance(來源檔 + 計算方式)")
    w("")
    w(f"- **效能(§2/§5)**:系統 `{a.results}/<contract>/run{{1..{n_runs}}}/system/eval/evaluation_results_system.csv`;"
      f"baseline `.../baselines/<method>_baseline/eval/evaluation_results.csv`。")
    w("- **macro / micro / combined(§1)**:對每個 run i,把 Online124-run_i 與 Online39-run_i 配成一組(共 "
      f"{n_runs} 組)。**micro** = 兩份 TP/FP/FN 合計後算 P=TP/(TP+FP)、R=TP/(TP+FN)、F1;**macro** = 兩份各自 "
      "P/R/F1 再取平均。表中 TP/FP/FN/成本/時間皆為兩份合計。最後對 "
      f"{n_runs} 組取 mean±std[min–max]。")
    w(f"- **Funnel(§3/§4/§6)**:`{a.results}/<contract>/run{{1..{n_runs}}}/system/analysis/funnel.json`。"
      "6a 輸入 = n_reg_norms × n_contract_norms,通過 = 交集非空配對數;6b 輸入 = 6a 通過,通過 = cosine≥thr "
      "survivors;6c 輸入 = 6b 通過,通過 = Compliant,篩除 = Violation+Gap。§3 為兩份逐 run 合計後取 mean[min–max]。")
    w(f"- **成本**:`.../system/cost/main_usage.json`、`.../baselines/<m>_baseline/cost/<m>_usage.json` 的 token "
      "用量 × `config.yaml` pricing。**時間**:`.../system/time/step_times.json`、`.../baselines/<m>_baseline/time/time.json`。")
    w(f"- **參數 / KG db**:各 run 的 `{a.results}/<contract>/run<N>/run_meta.json`。")
    w("")

    out = RES / "FINAL_REPORT.md"
    out.write_text("\n".join(L) + "\n", encoding="utf-8")
    print(f"[final-report] 已寫 {out}")


if __name__ == "__main__":
    main()
