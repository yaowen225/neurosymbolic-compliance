"""
Step 3 (Regulatory): Norm Extractor for Regulatory Documents

從 parsed regulatory JSON 逐條抽取 norm(s)。一條 clause 可能產生多個 norm,
因為 enumerated 列舉 (R8 的 (a)(b)(c)(d)) 會被 LLM 展開成子 norm。

enumerated 列舉處理:
  - 父 norm 為必填:每個有內部列舉的 clause 一定產出一個父 norm(id = clause_id,
    belongs_to = null),承載 logic_type 與「父層級的整體/隱含義務」。
  - 父 norm 與所有子 norm 都會進入 Step 6 比對主迴圈;空泛的引言型父 norm 會在
    6a/6b 自然配不到而淘汰,不需額外旗標排除。

程式負責(不經 LLM):
  - source_text: 一律填該條法規的「完整原文」(R8_a/b/c/d 都填 R8 完整原文)
  - obligation_groups: 由 Step 2 的 assignments 對應填入,子 norm 繼承父 clause 的 groups

clause_id / belongs_to 解讀(見 IMPLEMENTATION_PROGRESS.md 待辦 #1):
  - canonical clause_id = LLM 回傳的 "id"  (非 enum = R3;父 = R8;子 = R8_a)
  - belongs_to          = LLM 回傳的 "belongs_to" (子 = R8;否則 null)

依賴:Step 2 obligation_groups JSON 必須先產生。若重跑 Step 2,須一併重跑本檔。

使用方式:
    python extract_regulatory.py
    python extract_regulatory.py --input ... --groups ... --output-dir ...
"""

import os
import re
import json
import argparse
import sys
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()

# 成本計算 + 生成 LLM 後端切換(預設不設 env 時等同原本)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import cost_meter
import core_sentence_postproc
import gen_runtime


# ==================== 實驗參數 (Experimental Parameters) ====================
LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
MAX_RETRIES = 3

INPUT_PATH = "../../output/parsed_regulatory/GDPR_DPA_Requirements_parsed.json"
GROUPS_PATH = "../../output/obligation_groups/GDPR_DPA_Requirements_obligation_groups.json"
OUTPUT_DIR = "../../output/norms"
FAILURE_DIR = "../../output/failures"
# ====================================================


# ==================== 預設參數 ====================
API_TIMEOUT = 60
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10
# ====================================================


# norm schema 的核心 / qualifier 欄位(供後處理逐一搬運,確保欄位齊全)
CORE_FIELDS = ["actor", "action", "object", "recipient"]
QUALIFIER_FIELDS = ["condition", "timing", "manner", "target", "location", "cause"]


def synthesize_missing_parents(norms, source_text, obligation_groups,
                               origin_clause=None, parser_parent=None):
    """
    父 norm 防呆:若有子 norm(belongs_to 非空)卻缺對應的父 norm,程式自動補一個,
    確保不會有孤兒子 norm,且 KG 的 HAS_CHILD 建得起來。

    補出的父 norm:
      - clause_id = belongs_to 指向的值;belongs_to = None;logic_type = "AND"(預設)
      - core_sentence = 子 norm core_sentence 的綜述
      - actor/action/object/recipient/modality 取第一個子 norm(best-effort,供 6c)
      - source_text 與子相同(由呼叫端傳入該 clause 全文)
      - obligation_groups 取子 norm groups 的聯集(退而求其次用傳入的 groups)
    """
    child_targets = {n["belongs_to"] for n in norms if n.get("belongs_to")}
    norm_ids = {n["clause_id"] for n in norms}
    for missing in sorted(child_targets - norm_ids):
        kids = [n for n in norms if n.get("belongs_to") == missing]
        if not kids:
            continue
        objs = [k.get("object") for k in kids if k.get("object")]
        cores = [k.get("core_sentence") for k in kids if k.get("core_sentence")]
        union_groups = []
        for k in kids:
            for g in (k.get("obligation_groups") or []):
                if g not in union_groups:
                    union_groups.append(g)
        parent = {
            "clause_id": missing,
            "belongs_to": None,
            "logic_type": "AND",
            "actor": kids[0].get("actor"),
            "action": kids[0].get("action"),
            "object": "; ".join(objs) if objs else kids[0].get("object"),
            "recipient": kids[0].get("recipient"),
            "modality": kids[0].get("modality"),
            "condition": None, "timing": None, "manner": None,
            "target": None, "location": None, "cause": None,
            "core_sentence": " ".join(cores) if cores else None,
            "source_text": source_text,
            "obligation_groups": union_groups or list(obligation_groups or []),
        }
        if origin_clause is not None:
            parent["origin_clause"] = origin_clause
            parent["parent"] = parser_parent
        norms.append(parent)
        print(f"   [防呆] 補上缺失父 norm: {missing}")
    return norms


def strip_leading_dash(text: str) -> str:
    """
    清掉法規條款開頭的「- 」前綴。
    法規 parser 沿用 src_old 切法,clause 文字會留下原文 "R8 - The..." 中的 "- ";
    這裡在抽取階段移除,讓 source_text 與送進 LLM 的文字都乾淨。
    """
    if not isinstance(text, str):
        return text
    return re.sub(r'^\s*[-–—]\s*', '', text)


# Prompt 內含大量字面 JSON 大括號,因此用 .replace 注入佔位符,不用 str.format。
EXTRACTION_PROMPT = """You are extracting structured obligations from a single clause of a regulatory document.
Work ONLY from the text you are given; do not rely on outside knowledge of any specific law.

You will be given ONE clause (with its clause_id and full text). Produce a JSON object
describing one or more "norms" extracted from this clause.

## What is a norm
A norm is a single, atomic obligation, permission, or prohibition. Each norm has these fields:

- actor: the entity that performs the action, AS WRITTEN. Do NOT infer a different party.
  If the clause is a noun phrase describing required CONTENT (e.g. an item in a list such as
  "a summary of the findings", "the name of Y") and names no acting party, set actor = null —
  do NOT invent an actor and do NOT treat the content noun itself ("a summary") as the actor.
- action: the core verb. If the clause is a noun-phrase content item with no real verb
  (e.g. "a summary of the findings"), do NOT fabricate a verb like "summarize"; use "include"
  (the item is something to be included/provided) and keep the full noun phrase in object.
- object: WHAT the action operates on / its content. ONLY the thing acted upon; not the
  recipient. Keep as a single phrase; do not split internal "of ..." / "including ..." parts.
  For a noun-phrase content item, put the WHOLE phrase here (e.g. object = "a summary of the
  findings").
- recipient: the receiver or direction ("to whom / toward whom"). null if none.
- modality: the modal expression, ONLY the modal verb itself (e.g. "shall", "may", "must",
  "shall not", "can", or "plain statement" if the obligation is a plain declarative with no
  modal verb). Do NOT convert it to a category. Do NOT include adverbs or scope words:
  time words ("promptly", "without undue delay", "within 72 hours") go in `timing`; scope
  words like "at least" must be dropped (the "all items required" sense is carried instead by
  logic_type = "AND").
- condition: the triggering precondition. null for standing obligations with no condition.
- timing: time requirement (deadline, frequency, time limit). null if none.
- manner: how / in what form / for what purpose. null if none.
- target: direction, destination, start/end point. null if none.
- location: place. null if none.
- cause: reason. null if none.
- core_sentence: ONE fluent English sentence built ONLY from the non-null actor, action,
  object, recipient of THIS norm. If actor is null (a content item), phrase it as the parent
  duty applied to this item, e.g. "The required document includes a summary of the findings."
  Do NOT include condition/timing/manner/etc.

## Expanding a clause into parent + child norms

A clause may need to be expanded into one PARENT norm plus several CHILD norms.
There are two distinct triggers. Treat the FIRST as a strict, always-apply rule.

### Trigger 1 (STRICT): an explicit enumerated list
If the clause contains an explicit lettered/numbered list of items, e.g.
"... include (a) ... (b) ... (c) ... (d) ...", you MUST expand it:
- one PARENT norm: id = clause_id, belongs_to = null, core_sentence summarizing the whole
  requirement. Set logic_type by the parent's wording:
    * "shall (at least) include / must include" -> the items are all required -> "AND".
    * "can include / may include / such as / for example / including" -> the items are
      illustrative, NOT all mandatory -> "OR".
- one CHILD norm for EACH listed item (a), (b), (c), ...: id = "<clause_id>_a",
  "<clause_id>_b", ...; belongs_to = clause_id. IMPORTANT: each child item inherits the
  PARENT's action and binding force. The child's action/object describe that item AS PART OF
  the parent duty (e.g. if the parent is "the document shall include the following", a child
  item "a summary of the findings" becomes action = "include", object = "a summary of the
  findings", with the parent's actor). Do NOT fabricate a new verb unrelated to the parent.
Do NOT collapse the list into a single norm's object.

### Trigger 2: multiple independent obligations in prose
If the clause states two or more independently-standing obligations NOT written as a lettered
list (e.g. two separate duties in one sentence), expand the same way: one parent norm
(logic_type = "AND") + one child norm per independent obligation (letter-suffixed ids,
belongs_to = clause_id), each keeping its own modality.
Decide by this test: are there two or more obligations that can each STAND and be SATISFIED
on their own (different action/object, possibly different modality)? If yes, expand.

### When NOT to expand
If the clause states a single obligation, output ONE norm (id = clause_id, belongs_to = null,
logic_type = null). Do not over-split a single obligation:
- "notify the controller of a breach" is ONE obligation (do not split into notify + breach).
- Multiple verbs joined by "and" that describe the SAME single duty are ONE obligation
  (e.g. "allow for and contribute to audits" is one obligation about audits — do NOT split).
- Alternatives joined by "or" are ONE obligation with a choice (e.g. "return or delete the
  data" is one obligation — do NOT split into two).
- A detail or qualifier of one action stays inside that one norm.

## Output format
Return a single JSON object:
{
  "norms": [
    {
      "id": "<clause_id, or clause_id_a / clause_id_b ... for expanded items>",
      "clause_id": "<clause_id>",
      "belongs_to": "<parent clause_id or null>",
      "logic_type": "AND" | "OR" | null,
      "actor": "... or null", "action": "...", "object": "...", "recipient": "... or null",
      "modality": "... or null",
      "condition": "... or null", "timing": "... or null", "manner": "... or null",
      "target": "... or null", "location": "... or null", "cause": "... or null",
      "core_sentence": "..."
    }
  ]
}
Return ONLY the JSON object. No prose, no markdown fences.
(Note: source_text and obligation_groups are filled in by the program afterwards; do not
produce them. The program also assigns the same verbatim source_text to a parent and all its
children. For an expansion you only need to set belongs_to as described above.)

## Clause
clause_id: {clause_id}
text: {clause_text}
"""


class RegulatoryNormExtractor:
    """法規端 norm 抽取器"""

    def __init__(self, api_key: str):
        self.client = gen_runtime.build_client(API_TIMEOUT)
        self.failures = []

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(
            multiplier=RETRY_WAIT_EXPONENTIAL_MULTIPLIER,
            max=RETRY_WAIT_EXPONENTIAL_MAX
        )
    )
    def _call_llm(self, clause_id: str, clause_text: str) -> Dict:
        prompt = (
            EXTRACTION_PROMPT
            .replace("{clause_id}", clause_id)
            .replace("{clause_text}", clause_text)
        )

        content = gen_runtime.chat(
            self.client,
            model=LLM_MODEL,
            reasoning_effort=REASONING_EFFORT,
            messages=[
                {"role": "system", "content": "You are a legal obligation extraction expert."},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )

        return json.loads(content)

    def extract_clause(self, clause: Dict, clause_groups: List[str]) -> List[Dict]:
        """
        抽取單一 clause,回傳該 clause 的 norm 列表(已完成程式端後處理)。
        """
        clause_id = clause["clause_id"]
        clause_text = strip_leading_dash(clause["text"])   # 清掉開頭「- 」前綴

        raw = self._call_llm(clause_id, clause_text)
        raw_norms = raw.get("norms", []) or []

        norms = []
        for rn in raw_norms:
            # canonical clause_id = LLM 的 "id";退而求其次用內層 clause_id 或原 clause_id
            cid = rn.get("id") or rn.get("clause_id") or clause_id
            belongs_to = rn.get("belongs_to")

            norm = {
                "clause_id": cid,
                "belongs_to": belongs_to,
                "logic_type": rn.get("logic_type"),
            }
            for f in CORE_FIELDS:
                norm[f] = rn.get(f)
            norm["modality"] = rn.get("modality")
            for f in QUALIFIER_FIELDS:
                norm[f] = rn.get(f)
            norm["core_sentence"] = rn.get("core_sentence")

            # 程式填入(不經 LLM)
            norm["source_text"] = clause_text                 # 整條法規完整原文
            norm["obligation_groups"] = list(clause_groups)   # 子 norm 繼承父 clause 的 groups

            norms.append(norm)

        # 父 norm 防呆:有子 norm 卻缺父 norm 時自動補上(法規端)
        norms = synthesize_missing_parents(
            norms,
            source_text=clause_text,
            obligation_groups=list(clause_groups),
        )
        return norms

    def extract_all(self, clauses: List[Dict], assignments: Dict[str, List[str]]) -> List[Dict]:
        all_norms = []
        total = len(clauses)
        print(f"開始抽取 {total} 條法規 clauses 的 norms...")

        for i, clause in enumerate(clauses, 1):
            cid = clause["clause_id"]
            print(f"處理中 [{i}/{total}]: {cid}")

            groups = assignments.get(cid, [])
            if not groups:
                print(f"    {cid} 在 obligation_groups 中沒有分類")

            try:
                norms = self.extract_clause(clause, groups)
                all_norms.extend(norms)
                n_child = sum(1 for n in norms if n.get("belongs_to"))
                if n_child:
                    print(f"   → 展開 {len(norms)} 個 norm "
                          f"(父 1 / 子 {n_child})")
            except Exception as e:
                self.failures.append({
                    "clause_id": cid,
                    "error": str(e),
                    "clause_text": clause["text"][:200],
                })
                print(f"抽取失敗 (已重試 {MAX_RETRIES} 次): {cid} - {str(e)}")

        print(f"\n成功產生 norms: {len(all_norms)} 條 (來自 {total - len(self.failures)}/{total} clauses)")
        print(f"失敗 clauses: {len(self.failures)}/{total}")
        print(f"   全部 {len(all_norms)} 條 norm(含父 norm)都會進入 Step 6 比對主迴圈")
        return all_norms


def main():
    parser = argparse.ArgumentParser(description="Step 3 (Regulatory): Extract norms + attach obligation_groups")
    parser.add_argument("--input", type=str, help="Parsed regulatory JSON path")
    parser.add_argument("--groups", type=str, help="Obligation groups JSON path (from Step 2)")
    parser.add_argument("--output-dir", type=str, help="Output directory")
    parser.add_argument("--failure-dir", type=str, help="Failure log directory")
    args = parser.parse_args()

    input_path = Path(args.input if args.input else INPUT_PATH)
    groups_path = Path(args.groups if args.groups else GROUPS_PATH)
    output_dir = Path(args.output_dir if args.output_dir else OUTPUT_DIR)
    failure_dir = Path(args.failure_dir if args.failure_dir else FAILURE_DIR)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY")
    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入文件: {input_path}")
    if not groups_path.exists():
        raise FileNotFoundError(
            f"找不到 obligation groups: {groups_path}\n"
            f"請先執行 Step 2: cd src/2_obligation_classifier && python regulatory_classifier.py"
        )

    output_dir.mkdir(parents=True, exist_ok=True)
    failure_dir.mkdir(parents=True, exist_ok=True)

    print(f"讀取 parsed clauses: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        clauses = json.load(f)

    print(f"讀取 obligation groups: {groups_path}")
    with open(groups_path, "r", encoding="utf-8") as f:
        groups_data = json.load(f)
    raw_assignments = groups_data.get("assignments", {})
    # 模型穩健性:有些模型(如 gpt-5.4-nano)會把 clause 顯示用的中括號也抄進 key,
    # 例如 "[R1]" 而非 "R1",導致 assignments.get("R1") 查不到 -> 法規端 groups 全空 -> 6a 0 配對。
    # 這裡把 key 正規化:去掉外圍中括號與空白,讓 R8 / [R8] / " R8 " 都對得上。
    assignments = {}
    n_brackets = 0
    for k, v in raw_assignments.items():
        nk = str(k).strip().strip("[]").strip()
        if nk != str(k):
            n_brackets += 1
        assignments[nk] = v
    print(f"   類別數: {len(groups_data.get('categories', []))}")
    print(f"   已分類 clauses: {len(assignments)}" +
          (f"(正規化 {n_brackets} 個含中括號/空白的 key)" if n_brackets else ""))

    cost_meter.configure(system="main", step="step3_extract_regulatory")
    extractor = RegulatoryNormExtractor(api_key)
    norms = extractor.extract_all(clauses, assignments)
    cost_meter.flush()

    # 後處理:重建 parser 列舉 child 的 core_sentence(actor==null + 有 parent + object 非空)
    _core_changed = core_sentence_postproc.rebuild_list_item_core_sentence(norms)
    print(f"\ncore_sentence 後處理:重建 {len(_core_changed)} 條列舉 child")
    for cid, _old, new in _core_changed:
        print(f"   {cid}: {new}")

    output_filename = input_path.stem.replace("_parsed", "_norms") + ".json"
    output_path = output_dir / output_filename

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(norms, f, indent=2, ensure_ascii=False)

    print(f"\nnorms 已輸出至: {output_path}")

    if extractor.failures:
        failure_path = failure_dir / "extraction_regulatory_failures.json"
        if failure_path.exists():
            with open(failure_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing.extend(extractor.failures)
            all_failures = existing
        else:
            all_failures = extractor.failures
        with open(failure_path, "w", encoding="utf-8") as f:
            json.dump(all_failures, f, indent=2, ensure_ascii=False)
        print(f"失敗記錄已輸出至: {failure_path}")

    if norms:
        first = norms[0]
        print("\n=== 預覽第一條 norm ===")
        print(f"clause_id: {first['clause_id']}  belongs_to: {first['belongs_to']}")
        print(f"core_sentence: {first['core_sentence']}")
        print(f"obligation_groups: {first['obligation_groups']}")


if __name__ == "__main__":
    main()
