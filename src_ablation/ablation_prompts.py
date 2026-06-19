"""
ablation_prompts.py — 「只換 6c 輸入型態」的控制變因 ablation 的四個 prompt(對齊到現行 main 6c)。

核心:四個變體**共用同一段實質判準**(SHARED;直接取自現行 main 6c 的最新規則 —— Steps、等價規則、
axis-match、pointer、placeholder、verdict consistency 等),只差在:
  - 給模型看什麼輸入(結構化欄位 / 原文 / 兩者),以及
  - 判斷以何者為主(main=結構化為主、hybrid=原文為主)。
這樣才是乾淨的控制變因(判準固定,只動輸入)。SHARED 由 import 現行 main prompt 即時擷取,
故 main 之後更新時,這裡也自動跟著對齊,不會再 drift。

四變體:
  main               結構化為主 + 原文為輔(= 現行 canonical 6c;此處僅作參照,實跑用 canonical 現成結果)
  structured_only    只給結構化欄位(不給原文)
  text_only          只給原文(不給結構化欄位)
  hybrid_textprimary 兩者都給,但「原文為主、結構化為輔」
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src" / "6_matching"))
import importlib.util as _u
_spec = _u.spec_from_file_location("_main6c", Path(__file__).resolve().parents[1] / "src" / "6_matching" / "stage_c_reasoning.py")
_m = _u.module_from_spec(_spec); _spec.loader.exec_module(_m)

MAIN_MODULE = _m                  # 讓 runner 重用同一份(避免 stage_c_reasoning 被 import 兩次重複包 stdout)
MAIN_PROMPT = _m.REASONING_PROMPT
STRUCT_FIELDS = _m.STRUCT_FIELDS

# 抽出「共用實質判準」段(input-agnostic):從 "Judge in ONE response" 到輸入區之前。
_a = MAIN_PROMPT.find("Judge in ONE response")
_b = MAIN_PROMPT.find("## Regulation requirement")
SHARED = MAIN_PROMPT[_a:_b].rstrip() + "\n\n"

# ---- 可重用的結構化欄位 / 原文 輸入區塊 ----
def _struct_block(side):
    return "Structured fields:\n" + "\n".join(f"{f}: {{{side}_{f}}}" for f in STRUCT_FIELDS)

# ---- 各變體的 intro 與 input ----
_INTRO_MAIN = MAIN_PROMPT[:_a]   # 結構化為主(原樣)

_INTRO_STRUCT = (
"You are a compliance reviewer checking whether a CONTRACT obligation satisfies a REGULATION\n"
"requirement. For each side you are given a set of structured fields (actor, action, object,\n"
"recipient, modality, condition, timing, manner, target, location, cause). You are NOT given the\n"
"original text; judge ONLY from the structured fields.\n\n"
"Base your judgment on the structured fields together with the substantive rules in the Steps below.\n\n")

_INTRO_TEXT = (
"You are a compliance reviewer checking whether a CONTRACT obligation satisfies a REGULATION\n"
"requirement. For each side you are given ONLY the original text of the obligation (no structured\n"
"fields). Read the original text to determine the obligation's actor, action, object, recipient,\n"
"modality, conditions and other constraints, then judge.\n\n"
"Base your judgment on the original text together with the substantive rules in the Steps below.\n\n")

_INTRO_HYBRID = (
"You are a compliance reviewer checking whether a CONTRACT obligation satisfies a REGULATION\n"
"requirement. For each side you are given a set of structured fields (actor, action, object,\n"
"recipient, modality, condition, timing, manner, target, location, cause) AND the original text.\n\n"
"Base your judgment PRIMARILY on the original text together with the substantive rules in\n"
"the Steps below. The structured fields play only a SUPPORTING role, limited to two situations:\n"
"(1) when the original text is ambiguous or incomplete on a point, use the structured fields to\n"
"clarify what the obligation actually is; (2) when the original text and a structured field\n"
"conflict, trust the original text. OUTSIDE of these two situations, do NOT let an isolated\n"
"structured field override the substantive judgment of the original text and the rules in the Steps.\n\n")

_INPUT_STRUCT = (
"## Regulation requirement\n" + _struct_block("reg") + "\n\n"
"## Contract obligation\n" + _struct_block("contract") + "\n{parent_context}")

_INPUT_TEXT = (
"## Regulation requirement\n"
"Original text: {reg_source_text}\n\n"
"## Contract obligation\n"
"Original text: {contract_source_text}\n{parent_context}")

_INPUT_HYBRID = (
"## Regulation requirement\n"
"Original text (PRIMARY): {reg_source_text}\n" + _struct_block("reg").replace("Structured fields:", "Structured fields (supporting reference only):") + "\n\n"
"## Contract obligation\n"
"Original text (PRIMARY): {contract_source_text}\n" + _struct_block("contract").replace("Structured fields:", "Structured fields (supporting reference only):") + "\n{parent_context}")

STRUCTURED_ONLY_PROMPT = _INTRO_STRUCT + SHARED + _INPUT_STRUCT
TEXT_ONLY_PROMPT = _INTRO_TEXT + SHARED + _INPUT_TEXT
HYBRID_TEXTPRIMARY_PROMPT = _INTRO_HYBRID + SHARED + _INPUT_HYBRID

# 變體設定:prompt + 是否用結構化/原文 + parent context 模式(struct/text/both)
VARIANTS = {
    "structured_only":    {"prompt": STRUCTURED_ONLY_PROMPT,    "use_struct": True,  "use_text": False, "parent": "struct"},
    "text_only":          {"prompt": TEXT_ONLY_PROMPT,          "use_struct": False, "use_text": True,  "parent": "text"},
    "hybrid_textprimary": {"prompt": HYBRID_TEXTPRIMARY_PROMPT, "use_struct": True,  "use_text": True,  "parent": "both"},
}
