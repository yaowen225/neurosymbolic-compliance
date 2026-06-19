"""
run_analysis.py — (C) 診斷分析 runner(單一自足腳本)

三組診斷的邏輯全部「內嵌」在本檔(下面三個 analyze_* 函式),共用工具去重成一份。
**不呼叫、不 import 任何外部腳本**(與已封存的 threshold_sensitivity.py /
sentence_level_diagnosis.py / pipeline_diagnostics.py 無任何執行期相依)。整包複製出去即可跑。

給定一個 run_pipeline.py 的輸出資料夾,跑三組診斷:
  1) threshold sensitivity   逐句、只看 6a+6b:不同 threshold 下有幾條 GT 句子活得下來
  2) sentence-level diagnosis 逐句漏斗:每條 GT Full 句子最終命中否,未命中漏在哪一關
                              (RECOVERED 以 compliant.csv 為準,= 正式評估的 TP)
  3) pipeline diagnostics     各關卡進出數量(FUNNEL)+ FN 清單 + FP 清單

全部為事後診斷,讀既有產物,不重跑 pipeline。輸出寫到 <output-dir>/analysis/。

用法:
  python run_analysis.py --output-dir runs/gpt-5.4-mini
"""

import os
import sys
import csv
import re
import json
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple, Optional
from collections import defaultdict
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
REG = "GDPR_DPA_Requirements"
CON = "Online124"
THRESHOLDS = [0.30, 0.35, 0.40, 0.45, 0.50, 0.55, 0.60, 0.65]

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")


# ---------------- 共用工具(原本三支各有一份,這裡合併) ----------------
def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace("“", '"').replace("”", '"').replace("„", '"')
    text = text.replace("‘", "'").replace("’", "'")
    text = re.sub(r"\s+", " ", text)
    return text.lower().strip()


def strip_clause_prefix(text: str) -> str:
    return re.sub(r"^\d+(\.\d+)+\s+", "", text)


def load_gt_sentences(gt_path: Path) -> List[Tuple[str, str]]:
    """[(rule_id, normalized_sentence), ...] 只取 Full,逐句。"""
    out = []
    with open(gt_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rule_id = (row.get("rule_id") or "").strip()
            sentences_str = row.get("corresponding_sentences") or ""
            degrees_str = row.get("matching_degrees") or ""
            if not sentences_str.strip():
                continue
            sentences = [s for s in sentences_str.split("\n") if normalize_text(s)]
            degrees = [d.strip() for d in degrees_str.split(",")]
            for i, sentence in enumerate(sentences):
                norm = normalize_text(strip_clause_prefix(sentence))
                if not norm:
                    continue
                degree = degrees[i] if i < len(degrees) else (degrees[-1] if degrees else "Full")
                if degree == "Full":
                    out.append((rule_id, norm))
    return out


def gt_dict(gt_sentences: List[Tuple[str, str]]) -> Dict[str, Set[str]]:
    d: Dict[str, Set[str]] = defaultdict(set)
    for rid, s in gt_sentences:
        d[rid].add(s)
    return d


def load_norms_emb(emb_path: Path) -> List[Dict]:
    return json.load(open(emb_path, encoding="utf-8"))["norms"]


def rule_of(pair: Dict) -> str:
    return pair.get("reg_belongs_to") or pair.get("reg_clause_id")


# ---------------- 1) Threshold sensitivity ----------------
def analyze_threshold_sensitivity(reg, con, gt_sentences, out_dir: Path, lines):
    def emit(s=""):
        print(s); lines.append(s)

    con_prepared = []
    for c in con:
        emb = c.get("embedding")
        con_prepared.append({
            "clause_id": c["clause_id"],
            "groups": set(c.get("obligation_groups") or []),
            "source_norm": normalize_text(c.get("source_text") or ""),
            "embedding": np.array(emb) if emb else None,
        })
    reg_by_rule: Dict[str, List[Dict]] = {}
    for n in reg:
        rule_id = n.get("belongs_to") or n["clause_id"]
        emb = n.get("embedding")
        reg_by_rule.setdefault(rule_id, []).append({
            "groups": set(n.get("obligation_groups") or []),
            "embedding": np.array(emb) if emb else None,
        })

    sentence_results = []  # (rule_id, sent, best_cosine|None, note)
    for rule_id, sent in gt_sentences:
        correct = [c for c in con_prepared if c["source_norm"] and sent in c["source_norm"]]
        reg_norms = reg_by_rule.get(rule_id, [])
        if not correct:
            sentence_results.append((rule_id, sent, None, "no_containment")); continue
        if not reg_norms:
            sentence_results.append((rule_id, sent, None, "no_reg_norm")); continue
        best, any_6a = None, False
        for rn in reg_norms:
            if rn["embedding"] is None:
                continue
            for c in correct:
                if c["embedding"] is None:
                    continue
                if rn["groups"] & c["groups"]:
                    any_6a = True
                    sim = float(np.dot(rn["embedding"], c["embedding"]))
                    if best is None or sim > best:
                        best = sim
        if best is None:
            sentence_results.append((rule_id, sent, None, "lost_at_6a" if not any_6a else "no_embedding"))
        else:
            sentence_results.append((rule_id, sent, best, "ok"))

    total = len(gt_sentences)
    survivable = sum(1 for *_, cosine, _ in sentence_results if cosine is not None)
    emit("=" * 78)
    emit("[1] THRESHOLD SENSITIVITY (逐句, 只看 6a+6b)")
    emit("=" * 78)
    emit(f"GT Full 句數: {total}")
    emit(f"\n{'threshold':<11}{'sentences_kept':<16}newly_lost_here")
    emit("-" * 50)
    rows, prev = [], survivable
    for t in THRESHOLDS:
        kept = sum(1 for *_, cosine, _ in sentence_results if cosine is not None and cosine >= t)
        emit(f"{t:<11.2f}{f'{kept}/{total}':<16}{prev - kept}")
        rows.append((t, kept, total, prev - kept)); prev = kept
    no_contain = sum(1 for *_, _, note in sentence_results if note == "no_containment")
    lost_6a = sum(1 for *_, _, note in sentence_results if note == "lost_at_6a")
    emit("-" * 50)
    emit(f"6a 能活下來的上界 = {survivable}/{total}  (no_containment={no_contain}, lost_at_6a={lost_6a})")

    with open(out_dir / "threshold_sensitivity.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["threshold", "sentences_kept", "sentences_total", "newly_lost_at_threshold"])
        for t, kept, tot, lost in rows:
            w.writerow([f"{t:.2f}", kept, tot, lost])
    with open(out_dir / "threshold_sensitivity_detail.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "best_cosine", "note", "gt_sentence"])
        for rid, sent, cosine, note in sentence_results:
            w.writerow([rid, f"{cosine:.4f}" if cosine is not None else "", note, sent])


# ---------------- 2) Sentence-level diagnosis ----------------
def analyze_sentence_diagnosis(compliant_csv, con_emb, stage_a, stage_b, stage_c, gt_sentences, out_dir, lines):
    def emit(s=""):
        print(s); lines.append(s)

    pred_by_rule: Dict[str, List[str]] = defaultdict(list)
    with open(compliant_csv, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pred_by_rule[(row.get("rule_id") or "").strip()].append(
                normalize_text(row.get("retrieved_sentence") or ""))

    con_src = {c["clause_id"]: normalize_text(c.get("source_text") or "") for c in con_emb}
    set_a = {(rule_of(p), p["contract_clause_id"]) for p in stage_a["pairs"]}
    set_b = {(rule_of(p), p["contract_clause_id"]) for p in stage_b["pairs"]}
    verd_c: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    for r in stage_c["results"]:
        verd_c[(r["rule_id"], r["contract_clause_id"])].append(r["verdict"])

    emit("\n" + "=" * 96)
    emit("[2] SENTENCE-LEVEL FUNNEL DIAGNOSIS (RECOVERED 以 compliant.csv 為準)")
    emit("=" * 96)
    rows, counts = [], defaultdict(int)
    emit(f"\n{'rule':<6}{'stage':<16}{'detail':<26} sentence")
    emit("-" * 96)
    for rule_id, sent in gt_sentences:
        recovered = any(sent in chunk for chunk in pred_by_rule.get(rule_id, []))
        if recovered:
            stage, detail = "RECOVERED", "in compliant.csv"
        else:
            cset = sorted({cid for cid, src in con_src.items() if src and sent in src})
            if not cset:
                stage, detail = "no_containment", "GT 句子不在任何合約 source_text"
            else:
                in_a = any((rule_id, cid) in set_a for cid in cset)
                in_b = any((rule_id, cid) in set_b for cid in cset)
                verdicts = [v for cid in cset for v in verd_c.get((rule_id, cid), [])]
                if verdicts:
                    stage = "lost_at_6c"
                    detail = "6c=" + "/".join(sorted(set(verdicts))) + " (未進 compliant.csv)"
                elif in_a and not in_b:
                    stage, detail = "lost_at_6b", "passed 6a, cosine < threshold"
                elif in_a:
                    stage, detail = "lost_at_6b", "in 6a, not in 6b/6c"
                else:
                    stage, detail = "lost_at_6a", "no group intersection"
        counts[stage] += 1
        rows.append([rule_id, stage, detail, sent])
        emit(f"{rule_id:<6}{stage:<16}{detail[:24]:<26} {sent[:46]}")

    total = len(gt_sentences)
    recovered_n = counts.get("RECOVERED", 0)
    emit("\n" + "-" * 96)
    for k in ["no_containment", "lost_at_6a", "lost_at_6b", "lost_at_6c", "RECOVERED"]:
        if counts.get(k):
            emit(f"   {k:<16}: {counts[k]:>3}  ({counts[k]/total*100:.1f}%)")
    emit(f"   sentence-level recall (= compliant.csv 命中) = {recovered_n}/{total} ({recovered_n/total*100:.1f}%)")

    with open(out_dir / "sentence_level_diagnosis.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "stage", "detail", "gt_sentence"])
        w.writerows(rows)


# ---------------- 3) Pipeline diagnostics (FUNNEL + FN/FP) ----------------
def analyze_pipeline_diagnostics(compliant_csv, stage_a, stage_b, stage_c, gtd, out_dir, lines):
    def emit(s=""):
        print(s); lines.append(s)

    # 讀 compliant.csv,以 (rule_id, contract_clause_id) 去重
    seen, preds = set(), []
    with open(compliant_csv, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rid = (row.get("rule_id") or "").strip()
            cid = (row.get("contract_clause_id") or "").strip()
            if (rid, cid) in seen:
                continue
            seen.add((rid, cid))
            preds.append({"rule_id": rid, "clause_id": cid,
                          "source_norm": normalize_text(row.get("retrieved_sentence") or "")})

    emit("\n" + "=" * 70)
    emit("[3] PIPELINE DIAGNOSTICS (FUNNEL + FN + FP)")
    emit("=" * 70)
    n_reg = stage_a.get("n_reg_norms")
    n_con = stage_a.get("n_contract_norms")
    full_pairs = (n_reg * n_con) if (n_reg and n_con) else None
    vc = stage_c.get("verdict_counts", {}) or {}
    n_rules = len({p["rule_id"] for p in preds})
    emit("\n[FUNNEL]")
    emit(f"6a group filter : 全配對 {full_pairs} (reg {n_reg} x con {n_con}) -> 交集非空 {stage_a.get('n_pairs')}")
    emit(f"6b semantic     : 輸入 {stage_b.get('n_input')} -> cosine >= {stage_b.get('threshold')} 通過 {stage_b.get('n_survivors')}")
    emit(f"6c reasoning    : 輸入 {stage_c.get('n_judged')} -> Compliant {vc.get('Compliant', 0)} "
         f"/ Violation {vc.get('Violation', 0)} / Gap {vc.get('Gap', 0)}")
    emit(f"Step7 aggregate : compliant.csv 去重後 {len(preds)} 列,涵蓋 {n_rules} 條 rule")

    pred_by_rule: Dict[str, List[str]] = defaultdict(list)
    for p in preds:
        pred_by_rule[p["rule_id"]].append(p["source_norm"])
    fn_rows = []
    for rule_id, sentences in gtd.items():
        chunks = pred_by_rule.get(rule_id, [])
        for s in sentences:
            if not any(s in chunk for chunk in chunks):
                fn_rows.append((rule_id, s))
    emit(f"\n[FN] 未被 compliant.csv 命中的 GT Full 句子({len(fn_rows)} 句):")
    for rid, s in sorted(fn_rows):
        emit(f"   [{rid}] {s[:80]}")

    fp_rows = []
    for p in preds:
        true_sents = gtd.get(p["rule_id"], set())
        if true_sents and any(s in p["source_norm"] for s in true_sents):
            continue
        fp_rows.append((p["rule_id"], p["clause_id"], p["rule_id"] in gtd))
    emit(f"\n[FP] 不對應任何 GT Full 句子的 compliant.csv 配對({len(fp_rows)} 筆):")
    for rid, cid, in_gt in sorted(fp_rows):
        tag = "rule in GT but clause not a GT sentence" if in_gt else "rule NOT in GT (no Full annotation)"
        emit(f"   [{rid}] {cid:<20} ({tag})")
    emit("=" * 70)


def main():
    ap = argparse.ArgumentParser(description="(C) 診斷分析 runner(三組診斷合併)")
    ap.add_argument("--output-dir", required=True, help="run_pipeline.py 的輸出資料夾")
    ap.add_argument("--contract", default=CON, help=f"合約名稱(預設 {CON});決定讀哪個 *_embeddings.json")
    ap.add_argument("--gt", default=str(ROOT / "evaluation" / "gt" / "online124_ground_truth.csv"))
    args = ap.parse_args()

    OUT = Path(args.output_dir).resolve()
    CR = OUT / "compliance_results"
    EMB = OUT / "embeddings"
    analysis_dir = OUT / "analysis"
    analysis_dir.mkdir(parents=True, exist_ok=True)

    gt_sentences = load_gt_sentences(Path(args.gt).resolve())
    gtd = gt_dict(gt_sentences)
    reg = load_norms_emb(EMB / f"{REG}_embeddings.json")
    con = load_norms_emb(EMB / f"{args.contract}_embeddings.json")
    stage_a = json.load(open(CR / "stage_a_pairs.json", encoding="utf-8"))
    stage_b = json.load(open(CR / "stage_b_pairs.json", encoding="utf-8"))
    stage_c = json.load(open(CR / "stage_c_results.json", encoding="utf-8"))
    compliant_csv = CR / "compliant.csv"

    lines: List[str] = []
    analyze_threshold_sensitivity(reg, con, gt_sentences, analysis_dir, lines)
    analyze_sentence_diagnosis(compliant_csv, con, stage_a, stage_b, stage_c, gt_sentences, analysis_dir, lines)
    analyze_pipeline_diagnostics(compliant_csv, stage_a, stage_b, stage_c, gtd, analysis_dir, lines)

    (analysis_dir / "analysis_report.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n[analysis] 全部結果已存到 {analysis_dir}")


if __name__ == "__main__":
    main()
