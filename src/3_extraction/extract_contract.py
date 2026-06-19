"""
Step 3 (Contract): Norm Extractor for Contract Documents (with obligation-group tagging)

對每條 parser 切出的合約 clause 抽取 norm,並由 LLM 對齊 Step 2 既定 taxonomy 標記
obligation_groups(multi-label;只能選不能發明;純 boilerplate 給 [])。

多重義務拆分(與法規端同一套機制):一個 clause 若含「兩個以上可獨立成立的義務」,
LLM 會回 父 norm(id = clause_id)+ 子 norm(id = clause_id_a/_b,belongs_to = clause_id),
每個子 norm 各自有 actor/action/object/recipient/modality/core_sentence。單一義務則回 1 個 norm。
拆分判斷準則寫在 prompt 裡,程式只負責後處理。

程式負責(不經 LLM):
  - source_text: 父與所有子都填「該 parsed clause 的逐字全文」(同一份;Step 8 子字串比對用)
  - origin_clause: = 該 parsed clause 的 clause_id(去重單位;Step7/8 同一 clause 多子 norm 配同一法規只算一次)
  - parent: 子 norm(有 belongs_to)= None;父/單一 norm = parser 的 parent(parser 子項層級)
    供 Step 5 KG writer 建 HAS_CHILD(belongs_to 或 parent)
  - obligation_groups 繼承:子條款 ∪ 祖先(belongs_to 或 parent 鏈)的 groups

依賴:Step 2 obligation_groups JSON(法規端 taxonomy)。若重跑 Step 2,須重跑本檔。

使用方式:
    python extract_contract.py
    python extract_contract.py --input ... --groups ... --output-dir ...
"""

import os
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
import gen_runtime
import core_sentence_postproc


# ==================== 實驗參數 ====================
LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
MAX_RETRIES = 3

INPUT_PATH = "../../output/parsed_contracts/Online124_parsed.json"
GROUPS_PATH = "../../output/obligation_groups/GDPR_DPA_Requirements_obligation_groups.json"
OUTPUT_DIR = "../../output/norms"
FAILURE_DIR = "../../output/failures"
# ====================================================


# ==================== 預設參數 ====================
API_TIMEOUT = 60
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10
# ====================================================


def _nullable_string():
    return {"type": ["string", "null"]}


# 合約端 norm schema(json_schema strict:單一物件)
NORM_SCHEMA = {
    "type": "object",
    "properties": {
        "clause_id": {"type": "string"},
        "actor": {"type": "string"},
        "action": {"type": "string"},
        "object": {"type": "string"},
        "recipient": _nullable_string(),
        "modality": _nullable_string(),
        "condition": _nullable_string(),
        "timing": _nullable_string(),
        "manner": _nullable_string(),
        "target": _nullable_string(),
        "location": _nullable_string(),
        "cause": _nullable_string(),
        "core_sentence": {"type": "string"},
        "obligation_groups": {
            "type": "array",
            "items": {"type": "string"}
        },
    },
    "required": [
        "clause_id", "actor", "action", "object", "recipient", "modality",
        "condition", "timing", "manner", "target", "location", "cause",
        "core_sentence", "obligation_groups"
    ],
    "additionalProperties": False
}


# Prompt 含字面 JSON 大括號,用 .replace 注入佔位符。
EXTRACTION_PROMPT = """You are extracting structured obligations ("norms") from a single clause of a data processing
contract, and classifying them into obligation groups. Work ONLY from the text you are given;
do not rely on outside knowledge of any specific law.

You will be given ONE contract clause (clause_id and its full text), plus a list of
obligation-group names with definitions. Produce a JSON object describing one or more norms.

## What is a norm
A norm is a single, atomic obligation, permission, or prohibition. Each norm has these fields:

- actor: the entity performing the action, AS WRITTEN. Do NOT infer a different party.
  If the clause is a noun phrase describing required CONTENT (e.g. a list item such as
  "a summary of the findings") and names no acting party, set actor = null — do NOT invent an
  actor and do NOT treat the content noun itself ("a summary") as the actor.
- action: the core verb. If the clause is a noun-phrase content item with no real verb
  (e.g. "a summary of the findings"), do NOT fabricate a verb like "summarize"; use "include"
  and keep the full noun phrase in object.
- object: WHAT the action operates on / its content. ONLY the thing acted upon; not the
  recipient. Keep as a single phrase; do not split internal parts. For a noun-phrase content
  item, put the WHOLE phrase here (e.g. object = "a summary of the findings").
- recipient: the receiver/direction ("to whom"). null if none.
- modality: the modal expression, ONLY the modal verb itself (e.g. "shall", "may", "must",
  "shall not", "can", or "plain statement" if the obligation is a plain declarative with no
  modal verb). Do NOT convert it to a category. Do NOT include adverbs or scope words:
  time words ("promptly", "without undue delay", "within 72 hours") go in `timing`; scope
  words like "at least" must be dropped (the "all items required" sense is carried instead by
  logic_type = "AND").
- condition: triggering precondition. null if none.
- timing: time requirement. null if none.
- manner: how / in what form / for what purpose. null if none.
- target: direction/destination/start-end. null if none.
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
"... include (a) ... (b) ... (c) ...", you MUST expand it:
- one PARENT norm: id = clause_id, belongs_to = null, core_sentence summarizing the whole
  requirement. Set logic_type by the parent's wording:
    * "shall (at least) include / must include" -> items all required -> "AND".
    * "can include / may include / such as / for example / including" -> items are
      illustrative, NOT all mandatory -> "OR".
- one CHILD norm for EACH listed item: id = "<clause_id>_a", "<clause_id>_b", ...;
  belongs_to = clause_id. IMPORTANT: each child item inherits the PARENT's action and binding
  force. The child's action/object describe that item AS PART OF the parent duty (e.g. if the
  parent is "the document shall include the following", a child item "a summary of the
  findings" becomes action = "include", object = "a summary of the findings", inheriting the
  parent's actor). Do NOT fabricate a new verb unrelated to the parent.
Do NOT collapse the list into a single norm's object.

### Trigger 2: multiple independent obligations in prose
If the clause states two or more independently-standing obligations NOT written as a lettered
list, expand the same way: one parent norm (logic_type = "AND") + one child norm per
independent obligation (letter-suffixed ids, belongs_to = clause_id), each keeping its own
modality.
Decide by this test: are there two or more obligations that can each STAND and be SATISFIED
on their own (different action/object, possibly different modality)? If yes, expand.

### When NOT to expand
If the clause states a single obligation, output ONE norm (id = clause_id, belongs_to = null,
logic_type = null). Do not over-split a single obligation:
- "notify the controller of a breach" is ONE obligation (do not split into notify + breach).
- Multiple verbs joined by "and" that describe the SAME single duty are ONE obligation
  (e.g. "allow for and contribute to audits" is one obligation — do NOT split).
- Alternatives joined by "or" are ONE obligation with a choice (e.g. "return or delete the
  data" is one obligation — do NOT split into two).
- A detail or qualifier of one action stays inside that one norm.

## Obligation group classification
For each norm, set obligation_groups to the group name(s) from the provided list that it
genuinely relates to (multi-label allowed). Use ONLY names from the provided list; do not
invent names. A norm with no group becomes invisible to downstream matching, so every norm
that carries any substantive obligation, permission, prohibition, or arrangement MUST receive
its closest applicable group(s) — assign the best-fitting group even when the fit is only
partial. AVOID empty arrays: when uncertain, assign one or more groups rather than [] — an
over-tagged norm is still recoverable downstream, but an empty one is invisible. PREFER
multi-label over too few.
A SHORT LIST-ITEM FRAGMENT is still substantive content and MUST be grouped: in particular a
fragment naming a CATEGORY OF DATA SUBJECT (e.g. "Children", "Business users", "Customers",
"Citizens", "Employees") or a TYPE / SPECIAL CATEGORY OF PERSONAL DATA (e.g. "national
identification number", "sensitive data", "criminal-conviction data") describes the PROCESSING
SCOPE (whose data / what data is processed) and MUST receive the processing-scope group(s) —
never [].
Return an empty array [] ONLY for a norm with NO obligation content at all: a pure
section heading, a definition of a defined term, a recital, or a bare cross-reference to
legislation. A norm stating the agreement's DURATION / TERM, the PARTIES or their contact
details, the PROCESSING SCOPE, or any substantive duty is NOT boilerplate and MUST be grouped;
do not return [] for it.

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
      "core_sentence": "...",
      "obligation_groups": ["...", "..."]
    }
  ]
}
Return ONLY the JSON object. No prose, no markdown fences.
(Note: source_text is filled in by the program from the verbatim parsed clause; do not
produce it. The program also fills the parser-level parent linkage and assigns the same
verbatim source_text to a parent and all its children. For a multi-obligation expansion you
only need to set belongs_to as described above.)

## Clause
clause_id: {clause_id}
text: {clause_text}

## Obligation group definitions
{obligation_groups_block}
"""


def synthesize_missing_parents(norms, source_text, origin_clause, parser_parent):
    """
    父 norm 防呆(合約端):有子 norm(belongs_to 非空)卻缺對應父 norm 時自動補一個,
    避免孤兒子 norm、確保 HAS_CHILD 建得起來。父 norm:
      clause_id = belongs_to 指向值;belongs_to = None;logic_type = "AND";
      actor/action/object/recipient/modality 取第一個子 norm;
      core_sentence = 子 norm core_sentence 綜述;source_text/origin_clause 與子相同;
      parent = parser_parent;obligation_groups = 子 norm groups 聯集。
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
        norms.append({
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
            "obligation_groups": union_groups,
            "source_text": source_text,
            "origin_clause": origin_clause,
            "parent": parser_parent,
        })
        print(f"   [防呆] 補上缺失父 norm: {missing}")
    return norms


def build_groups_block(categories: List[Dict]) -> str:
    """把 Step 2 的 categories 渲染成可讀的 prompt 區塊"""
    lines = []
    for cat in categories:
        name = cat.get("name", "<unnamed>")
        definition = cat.get("definition", "<no definition>")
        lines.append(f'  - "{name}"')
        lines.append(f"      Definition: {definition}")
    return "\n".join(lines)


class ContractNormExtractor:
    """合約端 norm + obligation_groups 抽取器"""

    def __init__(self, api_key: str, categories: List[Dict]):
        self.client = gen_runtime.build_client(API_TIMEOUT)
        self.failures = []
        self.categories = categories
        self.category_name_set = {c["name"] for c in categories if c.get("name")}
        self.groups_block = build_groups_block(categories)

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
            .replace("{obligation_groups_block}", self.groups_block)
        )

        content = gen_runtime.chat(
            self.client,
            model=LLM_MODEL,
            reasoning_effort=REASONING_EFFORT,
            messages=[
                {"role": "system",
                 "content": "You are a legal obligation extraction expert who also classifies "
                            "clauses into a fixed taxonomy."},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            # prompt 現在回 {"norms": [...]}(可能 parent + children),用 json_object 解析
            response_format={"type": "json_object"},
        )

        return json.loads(content)

    def _valid_groups(self, cats, clause_id):
        """過濾 LLM 自創/拼錯的 group,並去重。"""
        valid, invalid = [], []
        for c in (cats or []):
            if c in self.category_name_set:
                if c not in valid:
                    valid.append(c)
            else:
                invalid.append(c)
        if invalid:
            print(f"    {clause_id} 出現未知 group,已丟棄: {invalid}")
        return valid

    def extract_clause(self, clause: Dict) -> List[Dict]:
        """
        抽取單一 parsed clause,回傳 norm 列表。
        - 單一義務:LLM 回 1 個 norm(id = clause_id, belongs_to = null)。
        - 多重義務:LLM 回 父 norm(id = clause_id) + 子 norm(id = clause_id_a/_b,
          belongs_to = clause_id)。與法規端同一套機制。

        程式端後處理(不經 LLM):
        - canonical clause_id = LLM 的 "id"(父 = clause_id;子 = clause_id_a)
        - belongs_to = LLM 的 "belongs_to"(子 = clause_id;否則 null)
        - source_text:父與所有子都填「該 parsed clause 的逐字全文」(同一份)
        - origin_clause:= 該 parsed clause 的 clause_id(去重單位,Step7/8 用)
        - parent(parser 結構層級指標,供 KG HAS_CHILD + group 繼承):
            子 norm(有 belongs_to)→ parent = None(階層走 belongs_to)
            父/單一 norm           → parent = parser 給的 parent(parser 子項層級)
        """
        clause_id = clause["clause_id"]
        clause_text = clause["text"]
        parser_parent = clause.get("parent")

        raw = self._call_llm(clause_id, clause_text)
        raw_norms = raw.get("norms", []) or []

        # 判定 parent / children,並「重新產生」子 norm 的 id(不信任 LLM 的後綴):
        #   - 子 norm 後綴改用 "_ob_a/_ob_b/..." 連續字母(_ob_ = obligation-split level),
        #     從 a 開始連續、不跳號,且與 parser 切的層級(_main/_i/_a)清楚區分,
        #     避免 main_5.7.2_main_a 這種疊在 _main 上的混亂。
        #   - 父/單一 norm 的 clause_id 一律強制等於 parsed clause_id。
        non_children = [rn for rn in raw_norms if not rn.get("belongs_to")]
        with_belongs = [rn for rn in raw_norms if rn.get("belongs_to")]

        if len(raw_norms) <= 1:
            parent_raw = raw_norms[0] if raw_norms else None
            child_raws = []
        elif with_belongs:
            # 正常:1 個父(belongs_to 空)+ 多個子;多餘的「無 belongs_to」norm 一律降為子
            parent_raw = non_children[0] if non_children else None
            child_raws = with_belongs + non_children[1:]
        else:
            # LLM 回多個 norm 但都沒標 belongs_to:第一個當父,其餘當子
            parent_raw = raw_norms[0]
            child_raws = raw_norms[1:]

        norms = []
        if parent_raw is not None:
            norms.append(self._build_norm(
                parent_raw, cid=clause_id, belongs_to=None,
                parent=parser_parent, clause_text=clause_text, origin=clause_id,
            ))
        for idx, cr in enumerate(child_raws):
            child_cid = f"{clause_id}_ob_{chr(97 + idx)}"   # _ob_a, _ob_b, ... 連續
            norms.append(self._build_norm(
                cr, cid=child_cid, belongs_to=clause_id,
                parent=None, clause_text=clause_text, origin=clause_id,
            ))

        # 父 norm 防呆:有子 norm 卻缺父 norm 時自動補上(合約端)
        norms = synthesize_missing_parents(
            norms,
            source_text=clause_text,
            origin_clause=clause_id,
            parser_parent=parser_parent,
        )
        return norms

    def _build_norm(self, rn, cid, belongs_to, parent, clause_text, origin):
        """從 LLM 回的單一 raw norm 組出標準 norm dict(id/belongs_to/parent 由程式指定)。"""
        return {
            "clause_id": cid,
            "belongs_to": belongs_to,
            "logic_type": rn.get("logic_type"),
            "actor": rn.get("actor"),
            "action": rn.get("action"),
            "object": rn.get("object"),
            "recipient": rn.get("recipient"),
            "modality": rn.get("modality"),
            "condition": rn.get("condition"),
            "timing": rn.get("timing"),
            "manner": rn.get("manner"),
            "target": rn.get("target"),
            "location": rn.get("location"),
            "cause": rn.get("cause"),
            "core_sentence": rn.get("core_sentence"),
            "obligation_groups": self._valid_groups(rn.get("obligation_groups"), cid),
            "source_text": clause_text,    # 父與所有子同一份逐字全文
            "origin_clause": origin,       # 去重單位
            "parent": parent,              # 子靠 belongs_to;父/單一用 parser parent
        }

    def extract_all(self, clauses: List[Dict]) -> List[Dict]:
        norms = []
        total = len(clauses)
        self.n_split_clauses = 0      # 被拆成多義務的 parsed clause 數
        print(f"開始抽取 {total} 條合約 clauses 的 norm + obligation_groups...")

        for i, clause in enumerate(clauses, 1):
            print(f"處理中 [{i}/{total}]: {clause['clause_id']}")
            try:
                clause_norms = self.extract_clause(clause)
                n_children = sum(1 for n in clause_norms if n.get("belongs_to"))
                if n_children:
                    self.n_split_clauses += 1
                    print(f"   -> 拆成 {len(clause_norms)} 個 norm(父 1 / 子 {n_children})")
                for n in clause_norms:
                    if not n["obligation_groups"]:
                        print(f"    {n['clause_id']} obligation_groups 為空 "
                              f"(boilerplate or no matching theme)")
                norms.extend(clause_norms)
            except Exception as e:
                self.failures.append({
                    "clause_id": clause["clause_id"],
                    "error": str(e),
                    "clause_text": clause["text"][:200],
                })
                print(f"抽取失敗 (已重試 {MAX_RETRIES} 次): {clause['clause_id']} - {str(e)}")

        n_children_total = sum(1 for n in norms if n.get("belongs_to"))
        print(f"\n成功處理 clauses: {total - len(self.failures)}/{total}")
        print(f"產生 norms: {len(norms)} 條(其中子 norm {n_children_total} 條,來自 {self.n_split_clauses} 個被拆的 clause)")
        print(f"失敗: {len(self.failures)}/{total}")

        # 統計 group 分佈
        cat_count = {}
        for n in norms:
            for c in n.get("obligation_groups", []):
                cat_count[c] = cat_count.get(c, 0) + 1
        if cat_count:
            print("\n合約 obligation_groups 分佈:")
            for name, cnt in sorted(cat_count.items(), key=lambda x: -x[1]):
                print(f"   {cnt:4d}  {name}")
        empty = sum(1 for n in norms if not n.get("obligation_groups"))
        print(f"   (obligation_groups 為空: {empty} / {len(norms)} 條)")

        return norms


def apply_group_inheritance(norms: List[Dict]) -> List[Dict]:
    """
    後處理:每個有 parent 的合約 norm,obligation_groups 改成
    「自己的 groups ∪ 所有祖先(往上追到根)的 groups」(聯集,保留自己的再加上父的)。

    動機:GT 的正確對應常落在子條款(如 5.8.1_i/ii),但子條款被獨立標到別的 group,
    沒繼承父條款(如 5.8.1_main 的 Compliance Demonstration),導致 6a 交集為空被殺。

    用抽取當下的「原始 groups」計算聯集(避免父子更新先後造成的順序效應)。
    """
    by_id = {n["clause_id"]: n for n in norms}
    original = {n["clause_id"]: list(n.get("obligation_groups") or []) for n in norms}

    def eff_parent(node):
        # 有效父指標:LLM 多義務拆分用 belongs_to;parser 子項層級用 parent
        return node.get("belongs_to") or node.get("parent")

    changed = []
    for n in norms:
        cid = n["clause_id"]
        result = list(original[cid])           # 先放自己的(保留順序)
        seen = set()
        pid = eff_parent(n)
        while pid and pid in by_id and pid not in seen:
            seen.add(pid)
            for g in original.get(pid, []):
                if g not in result:
                    result.append(g)
            pid = eff_parent(by_id[pid])        # 往上追到根(支援多層;belongs_to 或 parent)
        if result != original[cid]:
            changed.append((cid, sorted(set(result) - set(original[cid]))))
        n["obligation_groups"] = result

    print(f"\ngroup 繼承後處理:{len(changed)} 條子 norm 繼承了父條款的 group")
    for cid, added in changed:
        print(f"   {cid}  +{added}")
    empty = sum(1 for n in norms if not n.get("obligation_groups"))
    print(f"   (繼承後 obligation_groups 仍為空: {empty} / {len(norms)} 條)")
    return norms


def main():
    parser = argparse.ArgumentParser(description="Step 3 (Contract): Extract norms + tag obligation_groups")
    parser.add_argument("--input", type=str, help="Parsed contract JSON path")
    parser.add_argument("--groups", type=str, help="Obligation groups JSON path (from Step 2, regulatory side)")
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

    print(f"讀取 obligation groups (法規端 taxonomy): {groups_path}")
    with open(groups_path, "r", encoding="utf-8") as f:
        groups_data = json.load(f)
    categories = groups_data.get("categories", [])
    if not categories:
        raise ValueError("obligation_groups JSON 中沒有 categories,無法分類")
    print(f"   類別數: {len(categories)}")
    for cat in categories:
        print(f"     - {cat.get('name')}")

    cost_meter.configure(system="main", step="step3_extract_contract")
    extractor = ContractNormExtractor(api_key, categories)
    norms = extractor.extract_all(clauses)
    cost_meter.flush()

    # 後處理:子條款 obligation_groups 繼承父條款(聯集,往上追到根)
    norms = apply_group_inheritance(norms)

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
        failure_path = failure_dir / "extraction_contract_failures.json"
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


if __name__ == "__main__":
    main()
