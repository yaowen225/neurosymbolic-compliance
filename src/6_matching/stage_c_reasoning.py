REASONING_PROMPT = """You are a compliance reviewer checking whether a CONTRACT obligation satisfies a REGULATION
requirement. For each side you are given a set of structured fields (actor, action, object,
recipient, modality, condition, timing, manner, target, location, cause) AND the original text.

Base your judgment PRIMARILY on the structured fields together with the substantive rules in
the Steps below. The original text plays only a SUPPORTING role, limited to two situations:
(1) when a structured field is empty or clearly incomplete, use the original text to fill in
what the obligation actually is; (2) when a structured field clearly contradicts the original
text (the extractor made a mistake), trust the original text to correct that field. OUTSIDE of
these two situations, do NOT let the literal wording of the original text override the
substantive judgment rules in the Steps.

Judge in ONE response, following these steps in order. The judgment is DIRECTIONAL: the
contract must MEET OR EXCEED the regulation's requirement. A contract that is STRICTER than
the regulation still SATISFIES it; a contract that is LOOSER does NOT.

Apply reasonable domain knowledge of data processing agreements (DPAs) and GDPR.
Judge by SUBSTANCE, not by literal wording: two phrasings that refer to the SAME real-world
obligation count as aligned even if the words differ.

Topic vs obligation: being about the same general subject area, alone, is NOT enough — the
contract clause must address the SAME underlying obligation, not merely an adjacent duty in the
same area. BUT do NOT confuse a DIFFERENT WORDING/FRAMING of the same obligation with a
different obligation. In particular, the following ARE the same obligation and DO satisfy it:
  - A NEGATIVE or PROHIBITIVE framing satisfies the AFFIRMATIVE framing of the same duty
    (e.g. a prohibitive "may not do X except under condition C" satisfies an affirmative
    "shall do X only under condition C").
  - A clause that NAMES, STATES, DESCRIBES, or SPECIFIES the required item satisfies a
    requirement to CONTAIN / INCLUDE / SET OUT that item — what matters is that the required
    information or commitment is present in the contract, not which verb is used.
  - A clause describing what is or will be done satisfies a requirement to record or specify
    that same thing.
  - An INFORMATION / AUDIT duty delivered with NARROWER-SOUNDING WORDING is still the same
    obligation. A duty to MAKE AVAILABLE the information necessary to demonstrate compliance, or to
    ALLOW and CONTRIBUTE TO audits / inspections, is SATISFIED by a clause that makes records, logs,
    documentation, or the relevant information available to the controller (and/or the supervisory
    authority) for inspection or on request — do NOT reject because it says "records" instead of
    "information". Likewise, demonstrating compliance with the CONTRACT's OWN obligations is
    co-extensive with demonstrating compliance with the regulation's obligations (the DPA
    transposes them); do NOT treat a reference to the agreement's own obligations, or to a
    specific clause of it, as a materially narrower scope. Judge the OBJECT as genuinely looser only
    when the contract OMITS a substantive part of what must be provided, never merely because it
    uses narrower-sounding words, names a concrete artefact (records/logs), or cites the agreement's
    own clause numbers for the same duty.
  - When the requirement is to STATE / CONTAIN / SET OUT a TYPE, CATEGORY, KIND, or DURATION of
    something (e.g. the types of personal data, the categories of data subjects, the duration of
    processing), a clause that NAMES the class or ENUMERATES one or more concrete instances of it
    — a single instance or a list, even an open-ended one (e.g. introduced by "such as" or
    "including but not limited to") — SATISFIES it. Listing the actual members, or naming the
    class, IS stating the type/category. Do NOT withhold Compliant merely because the clause gives
    specific instances instead of an abstract label, lists only some members, or frames the list
    as non-exhaustive. This holds even for a SINGLE bare fragment naming one such instance (e.g.
    one data type, one special category of data, one category of data subject) with no surrounding
    sentence — naming the instance is itself stating the type/category; do NOT require a complete,
    labelled, or exhaustive enumeration, and do NOT downgrade it to Violation for being narrow.
    AXIS MATCH (a content clause must answer the axis actually asked): the named class or instance
    must be an instance of the SAME KIND of content the requirement asks for. A requirement for the
    TYPES / CATEGORIES OF PERSONAL DATA is met only by naming an actual kind of DATA (e.g. contact
    data, health data, location data); it is NOT met by a category of DATA SUBJECTS (i.e. a class of
    PERSONS rather than a kind of data), by a processing activity or service (e.g. a storage or
    technical-support service), by a sub-processor / supplier, or by a generic definition of what
    "personal data" means. Stating the GENERAL STATUTORY DEFINITION of personal data in the abstract
    (i.e. what counts as personal data under the law, rather than which kinds of data are actually
    processed) is NOT naming a type of personal data processed under the agreement, and does NOT
    satisfy a "types of personal data" requirement → "Gap".
    Symmetrically a requirement for the CATEGORIES OF DATA SUBJECTS is met
    only by naming a class of PERSONS, not a kind of data; a requirement for a specific PARTY's
    identity/contact details is met only by that party's details, not another entity's. When the
    clause supplies an item belonging to a DIFFERENT axis than the one required, that is NOT
    satisfaction → output "Gap". CORRECT-AXIS LEAD-IN STILL QUALIFIES: a clause that introduces the
    RIGHT axis (announcing that the relevant types/categories follow as an in-line list, with NO
    deferral to a named external appendix / schedule / other document) DOES state the type/category and is
    Compliant — even when the concrete members are itemised in separate sub-clauses. Apply the
    axis-mismatch, generic-definition, and label-echo exclusions ONLY to WRONG-axis content or to
    clauses that defer the values to a named external location; NEVER use them to reject a
    correct-axis in-line lead-in merely for lacking a concrete member in its own sentence.
Withhold "Compliant" only when there is NO real substantive overlap (purely topical/adjacent),
or when it is a GENUINELY different duty — not merely a different verb, framing, or breadth.

Primary locus (avoid over-assignment): assign this requirement to a clause only when that
clause's OWN substance DIRECTLY and PRIMARILY carries the obligation. Output "Gap" when:
  - the clause only touches this requirement's subject in passing or as background while its main
    thrust is a DIFFERENT obligation; or
  - the clause merely NAMES or LISTS which topics are covered, or states that the actual
    substance / details are SET OUT, SPECIFIED, or HANDLED ELSEWHERE (e.g. "the details are
    specified in the annex", "as set out in a separate schedule", "in accordance with another
    section") — pointing to where something is handled is NOT itself delivering it; or
  - the requirement is an UMBRELLA duty to adopt a whole set/category of measures and THIS clause
    merely implements ONE individual measure within that set, while a separate clause makes the
    general commitment to the set — credit the general-commitment clause, not each individual
    measure clause.
Note: this locus rule is about WHICH clause delivers an obligation/commitment OR the actual
content. For pure CONTENT requirements that ask the DPA to merely STATE a type/category/identity,
the clause that GIVES THE ACTUAL item(s)/member(s) (even a single bare instance) is the locus and
is Compliant per the enumeration rule above. BUT a clause that only names the topic/label and
states that the actual values are SET OUT IN A NAMED SEPARATE LOCATION (an annex, schedule, or
another section — e.g. "the relevant details are specified in the annex") is a POINTER, not the
content itself → output "Gap"; credit the clause that actually contains the members, not the one
that says where to find them.

Steps:
1. Core alignment: do actor / action / object / recipient point to the SAME real-world
   obligation? Use reasonable domain inference; do NOT require literal string match, and apply
   the equivalent-framing rules above.
   - Same-information principle: when BOTH sides are CONTENT requirements — both require that
     the SAME piece of information be included / recorded / provided — they ARE aligned even
     when that information sits in a different document vehicle. Focus on WHAT must be recorded,
     not WHICH document holds it. CRUCIAL LIMIT: this applies only when THIS clause ITSELF states
     the information. A clause that merely says the information "is provided / specified / set out /
     described / contained" in ANOTHER document, agreement, appendix, schedule, annex, or a
     different clause — without itself stating it here — is a POINTER and does NOT deliver the
     content → output "Gap" (credit the clause that actually states it, not the one that says where
     to find it). LABEL-ECHO IS A POINTER, NOT NAMING-THE-CLASS: merely RE-STATING the
     requirement's own label / heading words — e.g. "the types of personal data", "the categories
     of data subjects", "the contact details" — and then saying those "are specified in / set out
     in / provided in" an appendix, schedule, annex, a separate agreement, or another clause defers
     the actual values elsewhere → "Gap". The naming-the-class / enumeration credit requires an
     ACTUAL class or member to be named HERE (e.g. a concrete data type, or a named class of data
     subjects), never the bare label words plus a cross-reference. CARVE-OUT: a heading or lead-in that itself introduces an enumeration whose
     members appear IN THE SAME DOCUMENT — immediately following it, or as its own sub-items
     (parent context provided) — DOES contain the content and remains Compliant; that is in-line
     content, not a pointer elsewhere.
   - Action-vs-content boundary: the same-information principle applies ONLY when both sides are
     content requirements. If the REGULATION requires PERFORMING AN ACTION or providing
     ASSISTANCE TO ANOTHER PARTY (e.g. notifying a supervisory authority, communicating to data
     subjects, assisting the controller in carrying something out), then a contract clause that
     merely specifies the CONTENT of an internal record/register — without itself imposing that
     action or assistance — does NOT satisfy it; output "Gap". Likewise a pure action clause
     does not satisfy a pure content requirement.
   - Confidentiality/security locus: when the requirement concerns confidentiality, security, or
     a similar protective duty, do NOT require the contract to address it at the same locus —
     imposing the duty on the DATA, on the PERSONS, or on the PROCESS each counts.
   - Party-role direction: this contract is a Data Processor Agreement, so its obligations are
     performed by the PROCESSOR (or jointly). If the REGULATION requirement is an obligation of
     the CONTROLLER acting on its own (its actor is "the controller" and the duty is the
     controller's own), then a contract obligation performed by the PROCESSOR does NOT satisfy
     it — output "Gap". EXCEPTION: this does NOT apply to "assistance" duties where the
     processor assists the controller in doing something — those are genuine processor
     obligations and are NOT blocked by this rule. This exception holds even when the regulation
     phrases the underlying task as the CONTROLLER's (e.g. "the controller shall carry out a data
     protection impact assessment", "the controller shall respond to data subjects") but the
     GDPR requirement is that the processor ASSIST with it: a contract clause in which the
     processor undertakes to assist / help / support the controller with that task SATISFIES the
     assistance requirement. Do NOT reject it on the ground that the task itself is ultimately the
     controller's own responsibility — assisting is exactly the processor's role.
2. Condition compatibility: do the triggering conditions refer to substantially the same
   situation? Treat semantically or substantially equivalent conditions as compatible. A
   contract that adds a reasonable, expected qualifier (e.g. "if necessary and relevant",
   "if possible") is NOT considered weaker on that basis.
3. Modality compatibility: a regulatory obligation ("shall"/"must") must be met by an
   obligation of equal-or-greater force on the contract side. A contract that only PERMITS an
   action ("may" / "is permitted to" / "is entitled to") does NOT satisfy a regulatory "shall".
   - "may only", "may not", "shall only", "must not" are RESTRICTIVE, not permissive, and are
     of equal-or-greater force than "shall" — they satisfy a regulatory "shall". Do NOT treat
     them as mere permission.
   - Sub-item exception: when the contract obligation is a SUB-ITEM of a parent clause (parent
     context provided) and the parent is mandatory ("shall"/"must"), a sub-item written as a
     plain descriptive statement (e.g. "a description of X") STILL counts as mandatory.
   Only a genuine permission ("may" allowing free choice) or a genuinely weaker parent counts
   as weaker.
4. Other constraints (manner, timing, target, location, cause): judge by semantic or
   substantial equivalence, not literal wording. Timing that is equivalent-or-stricter
   satisfies the requirement (e.g. "without undue delay" SATISFIES "immediately"). Only fail
   when the contract is genuinely looser or contradictory.

Empty / placeholder content: if the contract clause is an unfilled TEMPLATE PLACEHOLDER or
boilerplate carrying no actual content (e.g. "[insert ...]", "[insert when relevant]", empty
brackets, "to be completed", "N/A") it delivers nothing and CANNOT satisfy a content requirement
→ output "Gap".

Final sanity check before "Compliant": confirm there is genuine substantive overlap between
what the requirement demands and what the clause delivers. Reject (Gap) ONLY when the overlap
is purely topical with no real substance in common, or the clause is a genuinely different
duty. Do NOT reject merely because the framing (positive vs negative), the verb, or the breadth
differs — if the required substance is present, that is Compliant.

Verdict consistency: if core alignment holds AND the condition, modality, and other-constraints
checks are each compatible / not-weaker, you MUST output "Compliant" — do not output "Violation"
or "Gap" when every check has passed. Conversely, "Violation" is only for a clause that DOES
address this requirement but is genuinely LOOSER or CONTRADICTORY; if the clause simply does not
address the requirement (including the axis-mismatch, pointer, and placeholder cases above), the
verdict is "Gap", not "Violation".

Then output a final verdict:
- "Compliant": the contract obligation satisfies (meets or exceeds) this regulation requirement.
- "Violation": the contract addresses this requirement but fails it (genuinely looser or contradictory).
- "Gap": the contract obligation does not actually address this requirement at all.

## Output format
Return a single JSON object:
{
  "core_alignment_check": "<actor/action/object/recipient: do they describe the same obligation>",
  "condition_check": "<condition>",
  "modality_check": "<modality>",
  "other_constraints_check": "<manner/timing/target/location/cause>",
  "verdict": "Compliant" | "Violation" | "Gap"
}
Return ONLY the JSON object. No prose, no markdown fences.

## Regulation requirement
Structured fields:
actor: {reg_actor}
action: {reg_action}
object: {reg_object}
recipient: {reg_recipient}
modality: {reg_modality}
condition: {reg_condition}
timing: {reg_timing}
manner: {reg_manner}
target: {reg_target}
location: {reg_location}
cause: {reg_cause}
Original text (supporting reference only): {reg_source_text}

## Contract obligation
Structured fields:
actor: {contract_actor}
action: {contract_action}
object: {contract_object}
recipient: {contract_recipient}
modality: {contract_modality}
condition: {contract_condition}
timing: {contract_timing}
manner: {contract_manner}
target: {contract_target}
location: {contract_location}
cause: {contract_cause}
Original text (supporting reference only): {contract_source_text}
{parent_context}"""

# ============================================================================
# Step 6c:主系統 6c(結構化為主、原文為輔)。這是唯一正式版(原 stage_c_reasoning_hybrid.py)。
#   - judge 餵雙方結構化欄位 + source_text(原文,作為輔助參考)。
#   - parent_context 給父條款的「結構化欄位 + 原文」。
#   - 輸出到 compliance_results/(不帶 variant 標籤;hybrid 即預設)。
#   ablation 兩個變體在 src_ablation/(structured_only / text_only),才帶各自標籤。
# prompt(上面 REASONING_PROMPT)由使用者維護,程式不改。
# ============================================================================

import os
import json
import argparse
import threading
import yaml
import sys
from pathlib import Path
from typing import Dict, List
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError
from openai import OpenAI
from tenacity import retry, stop_after_attempt, wait_exponential
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import cost_meter
import gen_runtime


# ==================== 實驗參數 ====================
LLM_MODEL = "gpt-5.4-mini"
TEMPERATURE = 0.0
REASONING_EFFORT = None   # None = 繼承 lib/gen_runtime.py 的 DEFAULT_REASONING_EFFORT(目前 "none");要單獨覆寫本步才設成非 None 值(none/low/medium/high)
MAX_RETRIES = 3

INPUT_PATH = "../../output/compliance_results/stage_b_pairs.json"
OUTPUT_DIR = "../../output/compliance_results"
FAILURE_DIR = "../../output/failures"
CONFIG_PATH = "../../config.yaml"
REGULATORY_DOC = "GDPR_DPA_Requirements"
CONTRACT_DOC = "Online124"
COST_SYSTEM = "main"
# ====================================================

API_TIMEOUT = 60
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10
STRUCT_FIELDS = ["actor", "action", "object", "recipient", "modality",
                 "condition", "timing", "manner", "target", "location", "cause"]
PARENT_CONTEXT_FIELDS = ["actor", "action", "object", "recipient", "condition", "timing", "manner"]


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)["neo4j"]


def fetch_structured(driver, database: str, document_id: str) -> Dict[str, Dict]:
    """從 KG 重建每條 norm 的結構化欄位 + 原文(與純結構化版同一查詢,含 source_text)。"""
    query = """
    MATCH (d:Document {document_id: $doc})-[:CONTAINS]->(n:Norm)
    OPTIONAL MATCH (n)-[:HAS_ACTOR]->(a)
    OPTIONAL MATCH (n)-[:HAS_ACTION]->(ac)
    OPTIONAL MATCH (n)-[:HAS_OBJECT]->(o)
    OPTIONAL MATCH (n)-[:HAS_RECIPIENT]->(rec)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_CONDITION]->(cond)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_TIMING]->(tim)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_MANNER]->(man)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_TARGET]->(tar)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_LOCATION]->(loc)
    OPTIONAL MATCH (n)-[:HAS_QUALIFIER]->(:Qualifier)-[:HAS_CAUSE]->(cau)
    RETURN n.clause_id AS clause_id, n.modality AS modality,
           n.belongs_to AS belongs_to, n.parent AS parent,
           n.origin_clause AS origin_clause, n.source_text AS source_text,
           a.value AS actor, ac.value AS action, o.value AS object, rec.value AS recipient,
           cond.value AS condition, tim.value AS timing, man.value AS manner,
           tar.value AS target, loc.value AS location, cau.value AS cause
    """
    out = {}
    with driver.session(database=database) as session:
        for r in session.run(query, doc=document_id):
            out[r["clause_id"]] = {k: r[k] for k in r.keys()}
    return out


def _fmt(v) -> str:
    return "null" if v is None or (isinstance(v, str) and not v.strip()) else str(v)


def build_parent_context(con_parent: Dict) -> str:
    """父脈絡:父條款的結構化欄位 + 原文(兩者都給)。"""
    if not con_parent:
        return ""
    lines = [
        "",
        "## Parent clause context (original text + structured fields)",
        "The contract obligation above is a SUB-ITEM of a larger parent clause. Treat the "
        "sub-item TOGETHER WITH this parent context as the contract's fulfillment of the "
        "requirement (the parent-level duty also counts as performed by the contract).",
        f"Original text: {_fmt(con_parent.get('source_text'))}",
    ]
    for f in PARENT_CONTEXT_FIELDS:
        lines.append(f"{f}: {_fmt(con_parent.get(f))}")
    return "\n".join(lines)


class Reasoner:
    def __init__(self, api_key: str):
        self.client = gen_runtime.build_client(API_TIMEOUT)
        self.failures = []

    @retry(stop=stop_after_attempt(MAX_RETRIES),
           wait=wait_exponential(multiplier=RETRY_WAIT_EXPONENTIAL_MULTIPLIER, max=RETRY_WAIT_EXPONENTIAL_MAX))
    def judge(self, reg: Dict, con: Dict, con_parent: Dict = None) -> Dict:
        prompt = REASONING_PROMPT
        for f in STRUCT_FIELDS:
            prompt = prompt.replace("{reg_" + f + "}", _fmt(reg.get(f)))
            prompt = prompt.replace("{contract_" + f + "}", _fmt(con.get(f)))
        prompt = prompt.replace("{reg_source_text}", _fmt(reg.get("source_text")))
        prompt = prompt.replace("{contract_source_text}", _fmt(con.get("source_text")))
        prompt = prompt.replace("{parent_context}", build_parent_context(con_parent))

        content = gen_runtime.chat(
            self.client,
            model=LLM_MODEL,
            reasoning_effort=REASONING_EFFORT,
            messages=[
                {"role": "system", "content": "You are a compliance entailment reviewer."},
                {"role": "user", "content": prompt},
            ],
            temperature=TEMPERATURE,
            response_format={"type": "json_object"},
        )
        return json.loads(content)


def main():
    parser = argparse.ArgumentParser(description="Step 6c: main 6c (structured-primary + text support)")
    parser.add_argument("--input", type=str, default=INPUT_PATH)
    parser.add_argument("--output-dir", type=str, default=OUTPUT_DIR)
    parser.add_argument("--failure-dir", type=str, default=FAILURE_DIR)
    parser.add_argument("--config", type=str, default=CONFIG_PATH)
    parser.add_argument("--regulatory-doc", type=str, default=REGULATORY_DOC)
    parser.add_argument("--contract-doc", type=str, default=CONTRACT_DOC)
    args = parser.parse_args()

    print("=" * 70)
    print("Step 6c: 主系統(結構化為主、原文為輔)")
    print("=" * 70)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"找不到 6b 輸出: {input_path}。請先執行 stage_b_semantic.py")
    with open(input_path, "r", encoding="utf-8") as f:
        pairs = json.load(f)["pairs"]
    print(f"讀取 6b 配對: {len(pairs)}")

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY")

    config = load_config(args.config)
    try:
        driver = GraphDatabase.driver(config["uri"], auth=(config["username"], config["password"]))
        driver.verify_connectivity()
        print("Neo4j 連線成功")
    except (ServiceUnavailable, AuthError) as e:
        raise ConnectionError(f"Neo4j 連線失敗: {e}")

    database = config.get("database", "neo4j")
    cost_meter.configure(system=COST_SYSTEM, step="step6c_reasoning")
    reasoner = Reasoner(api_key)
    results = []

    try:
        reg_fields = fetch_structured(driver, database, args.regulatory_doc)
        con_fields = fetch_structured(driver, database, args.contract_doc)
        print(f"   法規 norms: {len(reg_fields)} | 合約: {len(con_fields)}")

        total = len(pairs)
        # 並行度:env STAGE_C_WORKERS(預設 1 = 原本序列行為;判定逐對獨立,結果與序列等價)。
        workers = max(1, int(os.environ.get("STAGE_C_WORKERS", "1")))
        prog = {"n": 0}
        prog_lock = threading.Lock()

        def process(idx_pair):
            idx, p = idx_pair
            r_id, c_id = p["reg_clause_id"], p["contract_clause_id"]
            reg = reg_fields.get(r_id)
            con = con_fields.get(c_id)
            if not reg or not con:
                print(f" 缺欄位,跳過: {r_id} <-> {c_id}", flush=True)
                return idx, None
            parent_id = con.get("belongs_to") or con.get("parent")
            con_parent = con_fields.get(parent_id) if parent_id else None
            try:
                verdict = reasoner.judge(reg, con, con_parent)
            except Exception as e:
                reasoner.failures.append({"reg_clause_id": r_id, "contract_clause_id": c_id, "error": str(e)})
                print(f"判斷失敗: {r_id} <-> {c_id} - {e}", flush=True)
                return idx, None
            with prog_lock:
                prog["n"] += 1
                if prog["n"] % 25 == 0 or prog["n"] == total:
                    print(f"   進度 {prog['n']}/{total}", flush=True)
            return idx, {
                "reg_clause_id": r_id,
                "contract_clause_id": c_id,
                "reg_belongs_to": reg.get("belongs_to"),
                "rule_id": reg.get("belongs_to") or r_id,
                "contract_origin_clause": con.get("origin_clause") or c_id,
                "parent_context_used": bool(con_parent),
                "similarity": p.get("similarity"),
                "shared_groups": p.get("shared_groups", []),
                "verdict": verdict.get("verdict"),
                "core_alignment_check": verdict.get("core_alignment_check"),
                "condition_check": verdict.get("condition_check"),
                "modality_check": verdict.get("modality_check"),
                "other_constraints_check": verdict.get("other_constraints_check"),
                "contract_source_text": con.get("source_text"),
            }

        indexed = list(enumerate(pairs))
        if workers == 1:
            collected = [process(ip) for ip in indexed]
        else:
            print(f"   (6c 並行度 STAGE_C_WORKERS={workers})", flush=True)
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=workers) as ex:
                collected = list(ex.map(process, indexed))
        # 依輸入順序還原,過濾掉缺欄位/失敗者
        results = [r for _idx, r in sorted(collected, key=lambda x: x[0]) if r is not None]

        cost_meter.flush()

        from collections import Counter
        vc = Counter(r["verdict"] for r in results)
        print(f"\n6c 判定分佈: {dict(vc)}")

        output_dir = Path(args.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / "stage_c_results.json"
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump({"n_input": len(pairs), "n_judged": len(results),
                       "verdict_counts": dict(vc), "results": results},
                      f, indent=2, ensure_ascii=False)
        print(f"\n結果已儲存: {output_path}")

        if reasoner.failures:
            failure_dir = Path(args.failure_dir)
            failure_dir.mkdir(parents=True, exist_ok=True)
            with open(failure_dir / "stage_c_failures.json", "w", encoding="utf-8") as f:
                json.dump(reasoner.failures, f, indent=2, ensure_ascii=False)
    finally:
        driver.close()


if __name__ == "__main__":
    main()