"""
Step 8: Evaluation (Containment Matching, sentence-level)

評估方法沿用 src_old 的 containment matching,但對齊新架構的去重:
  - 新架構合約端有拆出的子 norm(_a/_b)與父子結構。Step 7 已用 origin_clause 去重,
    compliant.csv 的 contract_clause_id 欄位即為 origin_clause(原始 clause)。
  - 本評估再防呆去重一次:以 (rule_id, contract_clause_id) 為單位,同一原始 clause
    配到同一條法規只算一次,避免 TP/FP 因拆分而膨脹。

containment:
  - ground truth 只取 matching_degrees == "Full" 的句子(逐句)。
  - 一條 GT 句子若(normalize 後)是某條 Compliant 配對的 contract source_text 的子字串,
    即視為被命中(合約 source_text 是逐字原文,子字串才對得上)。

計數(SENTENCE-LEVEL 為主):
  - TP = 被命中的 GT Full 句子數(以「句」為單位)。
  - FN = 未被命中的 GT Full 句子數 = 總 Full 句數 - TP。
  - FP = 去重後的 Compliant 配對中,其 contract source_text 不包含該 rule 任何 GT Full
         句子者(含「該 rule 根本不在 GT(無 Full 句)」的所有配對)。
  - precision = TP/(TP+FP);recall = TP/(TP+FN);F1。
rule-level 僅附一行參考(會灌水,不作主要指標)。

使用方式:
    python evaluate_retrieval.py
    python evaluate_retrieval.py --predictions ... --ground-truth ... --output ...
"""

import csv
import re
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple
from collections import defaultdict

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ==================== GT 路徑 ====================
GROUND_TRUTH_PATH = "gt/online124_ground_truth.csv"

# ==================== 系統i/o位置 ====================
# PREDICTIONS_PATH = "../output/compliance_results/compliant.csv"
# OUTPUT_PATH = "kg_matching/evaluation_results.csv"
# ==================== 系統 hybrid i/o位置 ====================
# PREDICTIONS_PATH = "../output/compliance_results/variant_hybrid/compliant.csv"
# OUTPUT_PATH = "kg_matching/evaluation_results_hybrid.csv"
# ==================== 預設 i/o(通常由 --predictions/--output 覆寫;不要寫死到任何 runs 資料夾)====================
# 主要使用方式一律由 run_evaluation.py 傳 --predictions/--output(各 baseline 寫進
# <output-dir>/baselines/<method>_baseline/)。以下只是 standalone 直跑時的占位預設。
PREDICTIONS_PATH = "../output/compliance_results/compliant.csv"
OUTPUT_PATH = "../output/evaluation_results_system.csv"
# ===================================================


def normalize_text(text: str) -> str:
    if not isinstance(text, str):
        return ""
    text = text.replace('“', '"').replace('”', '"').replace('„', '"')
    text = text.replace('‘', "'").replace('’', "'")
    text = re.sub(r'\s+', ' ', text)
    return text.lower().strip()


def strip_clause_prefix(text: str) -> str:
    return re.sub(r'^\d+(\.\d+)+\s+', '', text)


def load_ground_truth(gt_path: str) -> Dict[str, Set[str]]:
    """rule_id -> set of normalized Full sentences(只取 matching_degrees == Full)。"""
    gt: Dict[str, Set[str]] = {}
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
                    gt.setdefault(rule_id, set()).add(norm)
    return gt


def load_predictions(pred_path: str) -> List[Dict]:
    """
    讀 compliant.csv,以 (rule_id, contract_clause_id) 去重(contract_clause_id 即 origin_clause)。
    回傳去重後的配對列表 [{rule_id, clause_id, source_norm}]。
    """
    seen = set()
    preds = []
    with open(pred_path, "r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            rule_id = (row.get("rule_id") or "").strip()
            clause_id = (row.get("contract_clause_id") or "").strip()
            key = (rule_id, clause_id)
            if key in seen:
                continue
            seen.add(key)
            preds.append({
                "rule_id": rule_id,
                "clause_id": clause_id,
                "source_norm": normalize_text(row.get("retrieved_sentence") or ""),
            })
    return preds


def evaluate(pred_path: str, gt_path: str, output_path: str):
    print("--- Step 8 Evaluation (containment matching, sentence-level) ---")
    gt = load_ground_truth(gt_path)
    preds = load_predictions(pred_path)

    total_gt = sum(len(s) for s in gt.values())

    # 預測:rule_id -> [contract source_text(normalized)]
    pred_by_rule: Dict[str, List[str]] = defaultdict(list)
    for p in preds:
        pred_by_rule[p["rule_id"]].append(p["source_norm"])

    # TP:逐句檢查 GT Full 句子是否被某條同 rule 的 Compliant 配對 source_text 包含
    tp = 0
    for rule_id, sentences in gt.items():
        chunks = pred_by_rule.get(rule_id, [])
        for s in sentences:
            if any(s in chunk for chunk in chunks):
                tp += 1
    fn = total_gt - tp

    # FP:去重後配對中,contract source_text 不含該 rule 任何 GT Full 句子者
    fp = 0
    for p in preds:
        true_sents = gt.get(p["rule_id"], set())
        if true_sents and any(s in p["source_norm"] for s in true_sents):
            continue  # 支撐了某個 TP,不算 FP
        fp += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    print("\n=== SENTENCE-LEVEL RESULTS ===")
    print(f"True Positives  (TP): {tp}")
    print(f"False Positives (FP): {fp}")
    print(f"False Negatives (FN): {fn}")
    print("-" * 30)
    print(f"Precision: {precision:.4f}")
    print(f"Recall:    {recall:.4f}")
    print(f"F1-Score:  {f1:.4f}")
    print("-" * 30)

    # 只存指標(TP/FP/FN + P/R/F1);診斷性輸出見 pipeline_diagnostics.py
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Metric", "Score"])
        for k, v in [("True Positives (TP)", tp), ("False Positives (FP)", fp),
                     ("False Negatives (FN)", fn),
                     ("Precision", round(precision, 4)), ("Recall", round(recall, 4)),
                     ("F1-Score", round(f1, 4))]:
            w.writerow([k, v])
    print(f"Results saved to: {out}")


def main():
    parser = argparse.ArgumentParser(description="Step 8: Evaluation (containment, sentence-level)")
    parser.add_argument("--predictions", default=PREDICTIONS_PATH)
    parser.add_argument("--ground-truth", default=GROUND_TRUTH_PATH)
    parser.add_argument("--output", default=OUTPUT_PATH)
    args = parser.parse_args()

    base = Path(__file__).parent
    pred = args.predictions if Path(args.predictions).is_absolute() else str(base / args.predictions)
    gt = args.ground_truth if Path(args.ground_truth).is_absolute() else str(base / args.ground_truth)
    out = args.output if Path(args.output).is_absolute() else str(base / args.output)

    if not Path(pred).exists():
        raise FileNotFoundError(f"找不到 predictions: {pred}(請先跑 Step 7 aggregator.py)")
    if not Path(gt).exists():
        raise FileNotFoundError(f"找不到 ground truth: {gt}")

    evaluate(pred, gt, out)


if __name__ == "__main__":
    main()