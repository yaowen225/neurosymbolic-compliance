"""
baseline_passage_classification.py — Passage-classification baseline(段落層級,忠實重現論文)

用途:忠實代表他人論文的 paragraph-level passage-classification 方法,讓本論文能比較
「requirement-centric」與「passage-classification」兩種框架。**prompt 與判定格式照論文原文**,
只改「為了和本 repo 評估可比較」的最小外圍。論文兩個實驗只取 paragraph 版(論文最佳配置),
不做 sentence 版。

== 論文原文(their_code.ipynb 的 paragraph level,cell-32)==
- 粒度(重點):**逐「句子」預測,段落只當 context**。原文迴圈 `for ... in df.iterrows()` 是逐句,
  每句 `call_gpt(sentence, context)`,context = 該句所屬段落 df_paragraphs 的 paragraph。
  **不是**把整段丟進去分類。每句一次 call、**單標籤**輸出(回單一 policy id 如 'R5',或 '0' 表 none)。
- system message = prompt_template + few_shot_prompt(本檔逐字保留;見 PROMPT_TEMPLATE / FEW_SHOT)。
- user message   = f"Context: {context}\nSentence: {sentence}\nPrediction:"(context=段落,sentence=句子)。
- none = '0';**不帶任何 justification / explanation**(prompt 明文 "Do not include any explanations")。
- temperature = 0。原文用 gpt-3.5-turbo。

== 只改的外圍(為了可比較性,其餘照原文)==
1. 規則集:論文 cell-32 的 prompt_template 把 policies 留成未代入的 {{policies}}(其原始 bug),
   故本檔把 GDPR Art.28 的 **R1–R46** 規則清單 inline 進 system message(= cell-21 的 Policies 區塊),
   IDs 與本 repo gt/online124_ground_truth.csv 完全一致 → 可直接比較。
2. 句子/段落來源:論文用 Online124.xlsx 的 df_sentences(句)與 df_paragraphs(段)。**本檔改用同一份**
   inputs/parsed_contracts/Online124_parsed.json:
   - **句子(預測單位)= parser 的每個 clause**(逐 clause 一次 call;與本 repo 28 條 sentence-level GT
     在 containment 下對得起來——已驗證 28 條 GT Full 句皆為某個 clause 文字的子字串)。
   - **段落(context)= 該 clause 的頂層章節**(main_1..main_N、sub_a、sub_b;= 該章節各 clause 文字串接)。
   這是刻意的可比較性改編,與論文用 Excel 既有句/段不同。
3. 模型:走 lib/gen_runtime.chat()(GEN_MODEL / GEN_BACKEND env 可換;預設 gpt-5.4-nano);
   reasoning effort = None,繼承 lib/gen_runtime 的集中 DEFAULT。成本 system="passage" -> passage_usage.json。
4. 輸出:evaluate_retrieval.py 吃的 compliant.csv schema(rule_id, contract_clause_id=clause id,
   retrieved_sentence=clause(句子)文字, retrieval_score 空);none/'0' 不輸出。
   多標籤只在 GT 端;論文 prompt 字面單標籤,照搬即忠實,評估本來就對著 GT 算。

加了 LLM 分類後本 baseline 是 model-dependent 的。沒有 retrieval、沒有 embedding。

使用方式(獨立可執行):
    python baseline_passage_classification.py --con-parsed <con_parsed.json> --out <out.csv>
"""

import os
import csv
import json
import sys
import re
import argparse
from pathlib import Path
from dotenv import load_dotenv

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8")

load_dotenv()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lib"))
import cost_meter
import gen_runtime


# ==================== 實驗參數 ====================
CLF_MODEL = "gpt-5.4"        # 預設分類模型;GEN_MODEL env 會覆蓋(與 naive/RAG 同慣例)
REASONING_EFFORT = None           # None = 繼承 lib/gen_runtime 的 DEFAULT_REASONING_EFFORT(目前 "none")
TEMPERATURE = 0.0                 # 照論文 cell-32 call_gpt 預設 temperature=0
API_TIMEOUT = 180

CON_PARSED = str(Path(__file__).resolve().parents[1] / "inputs" / "parsed_contracts" / "Online124_parsed.json")
OUTPUT_CSV = "baselines/passage_classification_compliant.csv"
# ===================================================

ALL_RULES = [f"R{i}" for i in range(1, 47)]   # R1 .. R46(與 GT rule 空間一致)
VALID_RULES = set(ALL_RULES)

# ---- 論文 cell-32 的 prompt_template(逐字保留;{{policies}}/{{text}}/{{context}} 是原文的字面內容)----
PROMPT_TEMPLATE = """
You are a legal expert trained to identify applicable {{policies}}  based on a given {{text}} within its specific {{context}}.
When provided with the {{text}} and its {{context}}, your response should only include the policy identifier (e.g., 'R5') if applicable.
If there is no direct connection to any policy within the context provided, respond with '0'.
Do not include any explanations or additional text. Follow this format strictly.
"""

# ---- 論文 cell-32 的 few_shot_prompt(逐字保留,含原文 typo "failure fto" 與 </stop> 標記)----
FEW_SHOT = """

--- example start ---
Text: {{[NAME ACCOUNTANCY PRACTICE], with its registered office in [city, street and house number], hereinafter referred to as: "Processor", "We", "Us" or "Our", duly represented in this matter by [name + position];}}
Prediction: R2</stop>

Text: {{The Processing will be carried out in accordance with Your written instructions, unless We are obliged by law or regulations to act differently (for example, when considering whether an "unusual transaction" should be reported within the context of the Money Laundering and Terrorist Financing Prevention Act (Wwft)).}}
Prediction: R12</stop>

Text: {{We shall not be liable for any damage suffered as a result of Your failure fto comply with the GDPR or other laws or regulations.}}
Prediction: R6</stop>

Text: {{Data Breach Notification Duty: The duty to report Data Breaches to the Dutch Data Protection Authority and (in some cases) to the Data Subject(s).}}
Prediction: 0</stop>

Text: {{It is not possible to terminate this Agreement prematurely.}}
Prediction: 0</stop>

Text: {{"Indemnification shall apply not only to the damage that Third Parties may have suffered (both material and immaterial), but also to the costs We must incur in connection therewith, for example in any legal proceedings, and to the costs of any fines imposed on Us as a result of Your acts."}}
Prediction: 0</stop>
--- example end ---
"""

# ---- 規則集(外圍):論文 cell-32 漏代入 policies,這裡 inline R1–R46(= cell-21 的 Policies 區塊)----
RULES_TEXT = """R1 - The DPA shall contain at least one controller's identity and contact details. (LL)
R2 - The DPA shall contain at least one processor's identity and contact details. (LL)
R3 - The DPA shall contain the duration of the processing. (Art. 28(3))
R4 - The DPA shall contain the nature and purpose of the processing. (Art. 28(3))
R5 - The DPA shall contain the types of personal data. (Art. 28(3))
R6 - The DPA shall contain the categories of data subjects. (Art. 28(3))
R7 - The organizational and technical measures to ensure a level of security can include: (a) pseudonymisation and encryption of personal data, (b) ensure confidentiality, integrity, availability and resilience of processing systems and services, (c) restore the availability and access to personal data in a timely manner in the event of a physical or technical incident, and (d) regularly testing, assessing and evaluating the effectiveness of technical and organisational measures for ensuring the security of the processing. (Art. 32(1))
R8 - The notification of personal data breach shall at least include (a) the nature of personal data breach; (b) the name and contact details of the data protection officer; (c) the consequences of the breach; (d) the measures taken or proposed to mitigate its effects. (Art. 33(3))
R9 - The DPIA shall at least include (a) a systematic description of the envisaged processing operations and the purposes of the processing, (b) an assessment of the necessity and proportionality of the processing operations in relation to the purposes, (c) an assessment of the risks to the rights and freedoms of data subjects, and (d) the measures envisaged to address the risks. (Art. 35(7))
R10 - The processor shall not engage a sub-processor without a prior specific or general written authorization of the controller. (Art. 28(2))
R11 - In case of general written authorization, the processor shall inform the controller of any intended changes concerning the addition or replacement of sub-processors. (Art. 28(2))
R12 - The processor shall process personal data on documented instructions from the controller. (Art. 28(3a))
R13 - The processor can process personal data without documented instructions, if required by Union or Member State law. (Art. 28(3a))
R14 - The processor shall inform the controller of that legal requirement before processing, if law does not prohibit informing the controller on grounds of public interest. (Art. 28(3a))
R15 - The processor shall ensure that persons authorized to process personal data have committed themselves to confidentiality or are under an appropriate statutory obligation of confidentiality. (Art. 28(3b))
R16 - The processor shall take all measures required pursuant to Article 32 or to ensure the security of processing. (Art. 28(3c))
R17 - The processor shall assist the controller in fulfilling its obligation to respond to requests for exercising the data subject's rights. (Art. 28(3e))
R18 - The processor shall assist the controller in ensuring the security of processing. (Art. 28(3f), Art. 32)
R19 - The processor shall assist the controller in consulting the supervisory authorities prior to processing where the processing would result in a high risk in the absence of measures taken by the controller to mitigate the risk. (Art. 28(3f), Art.36)
R20 - The processor shall assist the controller in notifying a personal data breach to the supervisory authority. (Art. 28(3f), Art.33)
R21 - The processor shall assist the controller in communicating a personal data breach to the data subject. (Art. 28(3f), Art.34)
R22 - The processor shall assist the controller in ensuring compliance with the obligations pursuant to data protection impact assessment (DPIA). (Art. 28(3f), Art.35)
R23 - The processor shall return or delete all personal data to the controller after the end of the provision of services relating to processing. (Art. 28(3g))
R24 - The processor shall immediately inform the controller if an instruction infringes the GDPR or other data protection provisions. (Art. 28(3h))
R25 - The processor shall make available to the controller information necessary to demonstrate compliance with the obligations Article 28 in GDPR. (Art. 28(3h))
R26 - The processor shall allow for and contribute to audits, including inspections, conducted by the controller or another auditor mandated by the controller. (Art. 28(3h))
R27 - The processor shall impose the same obligations referred to in Article 28(3) in GDPR on the engaged sub-processors by way of contract or other legal act under Union or Member State law. (Art. 28(4))
R28 - The processor shall remain fully liable to the controller for the performance of sub-processor's obligations. (Art. 28(4))
R29 - When assessing the level of security, the processor shall take into account the risk of accidental or unlawful destruction, loss, alternation, unauthorized disclosure of or access to the personal data transmitted, stored or processed. (Art. 32(2))
R30 - The processor shall not transfer personal data to a third country or international organization without a prior specific or general authorization of the controller. (Art. 28(3a))
R31 - The processor can demonstrate guarantees to Article 28 (1--4) through adherence to an approved codes of conduct or an approved certification mechanism. (Art. 28(5))
R32 - The processor shall implement appropriate technical and organisational measures to ensure a level of security appropriate to the risk of varying likelihood and severity for the rights and freedoms of natural persons. (Art. 32(1))
R33 - The processor shall ensure that any natural person acting under its authority who has access to personal data only process them on instructions from the controller. (Art. 32(4))
R34 - The processor shall notify the controller without undue delay after becoming aware of a personal data breach. (Art. 33(2))
R35 - A processor shall be liable for the damage caused by processing only where it has not complied with obligations of the GDPR specifically directed to processors or where it has acted outside or contrary to lawful instructions of the controller. (Art. 82(2))
R36 - In case of general written authorization, the controller shall have the right to object to changes concerning the addition or replacement of sub-processors, after having been informed of such intended changes by the processor. (Art. 28(2))
R37 - The controller shall have the right to suspend the processing in certain cases. (LL)
R38 - The controller shall have the right to terminate the DPA in certain cases. (LL)
R39 - The controller shall, no later than 72 hours after having become aware of it, notify the personal data breach to the supervisory authority. (Art. 33(1))
R40 - The controller shall document the personal breaches. (Art. 33(5))
R41 - In case of high risks, the controller shall communicate the data breach to the data subject without undue delay. (Art. 34(1))
R42 - The controller shall carry out DPIA. (Art. 35(1))
R43 - The controller shall seek advice of the DPO when carrying DPIA. (Art. 35(2))
R44 - The controller shall seek the views of data subjects or their representatives on the intended processing. (Art. 35(9))
R45 - The controller shall carry out a review to assess if processing is performed in accordance with the data protection impact assessment at least when there is a change of the risk represented by processing operations. (Art. 35(11))
R46 - Any controller involved in processing shall be liable for the damage caused by processing which infringes the GDPR. (Art. 82(2))"""

# system message = 論文 prompt_template + (外圍 inline 的)Policies + 論文 few_shot
SYSTEM_PROMPT = PROMPT_TEMPLATE + "\nPolicies: " + RULES_TEXT + "\n" + FEW_SHOT


def _resolve(base: Path, p: str) -> Path:
    # relative 路徑以「目前工作目錄(CWD)」為基準(符合 README 從 release/ 跑的範例);absolute 照用。
    # 預設值已是絕對路徑,故 base 不再使用(保留參數相容)。
    pp = Path(p)
    return pp if pp.is_absolute() else (Path.cwd() / pp)


def paragraph_key(clause_id: str) -> str:
    """把 parser 的 clause_id 聚合成「頂層章節」段落:
       main_5.1.1 -> main_5;main_2.1_main -> main_2;sub_a_1.1_i -> sub_a。"""
    if clause_id.startswith("main_"):
        rest = clause_id[len("main_"):]
        sec = rest.split(".")[0].split("_")[0]
        return f"main_{sec}"
    parts = clause_id.split("_")
    if len(parts) >= 2 and parts[0] == "sub":
        return f"sub_{parts[1]}"
    # 沒有 Appendix 前綴的合約(如 Online39,clause_id = "N.N" / "N.N_i"):依頂層章節 N 聚合。
    # (Online124 的 clause_id 一律有 main_/sub_ 前綴 -> 不會走到這裡 -> 不受影響。)
    m = re.match(r"^(\d+)\.", clause_id)
    if m:
        return f"sec_{m.group(1)}"
    return clause_id


def build_paragraphs(con):
    """回傳 [(paragraph_id, paragraph_text), ...](保留章節出現順序)。"""
    order, groups = [], {}
    for c in con:
        k = paragraph_key(c["clause_id"])
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append((c.get("text") or "").strip())
    return [(k, " ".join(t for t in groups[k] if t)) for k in order]


def parse_prediction(text):
    """論文輸出格式 = 單一 policy id 'R5'(或 '0' 表 none),無 justification。
    抽出回覆中的 R1–R46;'0' / 無 R-id -> none(空 list)。容忍模型偶爾回多個。"""
    t = (text or "").strip().replace("</stop>", " ")
    t = re.sub(r"^```(?:json)?", "", t).strip()
    t = re.sub(r"```$", "", t).strip()
    out = []
    for n in re.findall(r"[Rr]\s*(\d{1,2})", t):
        rid = f"R{int(n)}"
        if rid in VALID_RULES and rid not in out:
            out.append(rid)
    return out


def main():
    ap = argparse.ArgumentParser(description="Passage-classification baseline(忠實重現論文 paragraph 版,獨立可執行)")
    ap.add_argument("--con-parsed", default=CON_PARSED, help="切分後合約 JSON(step1 輸出)")
    ap.add_argument("--out", default=OUTPUT_CSV, help="輸出 compliant.csv 路徑")
    args = ap.parse_args()

    base = Path(__file__).parent
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY。請複製 .env.example -> .env(PowerShell:Copy-Item .env.example .env)並填入 key;見 README「安裝」。")

    con = json.load(open(_resolve(base, args.con_parsed), encoding="utf-8"))
    para_text = dict(build_paragraphs(con))   # paragraph_id -> 段落文字(逐句預測時當 context)
    print(f"合約 clause(句子,= 預測單位): {len(con)} | 段落(context): {len(para_text)} | "
          f"model={gen_runtime.resolve_model(CLF_MODEL)} backend={gen_runtime.backend()} "
          f"reasoning={'(DEFAULT)' if REASONING_EFFORT is None else REASONING_EFFORT}")

    client = gen_runtime.build_client(API_TIMEOUT)
    cost_meter.configure(system="passage", step="passage_clf")

    # 論文 paragraph-level:逐「句子」(= clause)一次 call,段落當 context
    rows, details, n_fail = [], [], 0
    for c in con:
        cid = c["clause_id"]
        sent = (c.get("text") or "").strip()
        if not sent:
            continue
        context = para_text.get(paragraph_key(cid), sent)   # 該句所屬頂層章節 = context
        user_msg = f"Context: {context}\nSentence: {sent}\nPrediction:"
        try:
            content = gen_runtime.chat(
                client, model=CLF_MODEL,
                messages=[{"role": "system", "content": SYSTEM_PROMPT},
                          {"role": "user", "content": user_msg}],
                temperature=TEMPERATURE,
                reasoning_effort=REASONING_EFFORT,
            )
            sat = parse_prediction(content)
        except Exception as e:
            print(f"  [classify 失敗] {cid}: {e}")
            content, sat, n_fail = "", [], n_fail + 1
        for rid in sat:
            rows.append([rid, cid, sent, ""])   # contract_clause_id=句子 clause id;retrieved_sentence=句子文字
        details.append({"clause_id": cid, "paragraph_id": paragraph_key(cid),
                        "sentence": sent, "prediction_raw": content, "parsed_rules": sat})
        print(f"  {cid}: {sat or 'none'}")
    cost_meter.flush()

    out = _resolve(base, args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["rule_id", "contract_clause_id", "retrieved_sentence", "retrieval_score"])
        w.writerows(rows)
    # 原文輸出(無 justification);存原始回覆方便檢視,不影響評估
    with open(out.with_name(out.stem + "_details.json"), "w", encoding="utf-8") as f:
        json.dump(details, f, indent=2, ensure_ascii=False)
    n_rules = len({r[0] for r in rows})
    print(f"\n輸出 {len(rows)} 列(涵蓋 {n_rules} 條 rule;classify 失敗 {n_fail} 段)-> {out}")


if __name__ == "__main__":
    main()
