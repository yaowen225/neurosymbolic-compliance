# Material Pack — 論文 / 簡報用素材(純資料)

本檔是從本 repo 實際輸出抽出的素材,給另一個對話串寫論文 / 做簡報用。**全部是真實資料**,
來源檔路徑標在各節。產生這次資料的設定:`GDPR_DPA_Requirements`(法規)對 `Online124` / `Online39`(合約),
gpt-5.4-mini,temperature 0,6b threshold 0.40。下列以 `results/Online124/run1/` 與 `results/Online39/run1/`
為代表(同一支 pipeline 的真實一次輸出)。

---

## 1. Norm schema —— 一句話被拆成什麼(含列舉拆解)

每條 norm 把一句法律文字拆成結構化欄位:

| 層 | 欄位 | 說明 |
|---|---|---|
| 核心命題 | `actor`(agent)/ `action`(predicate)/ `object`(theme)/ `recipient` | 誰、做什麼、對什麼、給誰 |
| 情態 | `modality` | shall / must / can / may / plain statement … |
| 限定 | `condition` / `timing` / `manner` / `target` / `location` / `cause` | 條件與方式等 qualifier |
| 溯源 | `clause_id` / `source_text` / `core_sentence` | 識別碼、逐字原文、給 embedding 用的單句 |
| 結構 | `belongs_to` / `parent` / `logic_type` / `obligation_groups` | 列舉父子、AND/OR、義務群組 |

**列舉拆解**:像 `(a)(b)(c)(d)` 這種一句含多個並列項的條文,會拆成「父 norm + 子 norm」——
父帶 `logic_type`(AND/OR),子用 `belongs_to`(法規端)或 `parent`(合約端)掛回父。

### 1a. 法規 norm 範例(R7:Art. 32(1) 安全措施清單;父 + 一個子)
來源:`results/Online124/run1/system/norms/GDPR_DPA_Requirements_norms.json`

父 `R7`(OR;一句含 (a)(b)(c)(d) 四項措施):
```json
{
  "clause_id": "R7",
  "belongs_to": null,
  "parent": null,
  "logic_type": "OR",
  "actor": null,
  "action": "include",
  "object": "the organizational and technical measures to ensure a level of security",
  "recipient": null,
  "modality": "can",
  "condition": null, "timing": null, "manner": null, "target": null, "location": null, "cause": null,
  "obligation_groups": ["Security Of Processing"],
  "core_sentence": "The organizational and technical measures to ensure a level of security can include the following items.",
  "source_text": "The organizational and technical measures to ensure a level of security can include: (a) pseudonymisation and encryption of personal data, (b) ensure confidentiality, integrity, availability and resilience of processing systems and services, (c) restore the availability and access to personal data in a timely manner in the event of a physical or technical incident, and (d) regularly testing, assessing and evaluating the effectiveness of technical and organisational measures for ensuring the security of the processing. (Art. 32(1))"
}
```

子 `R7_c`(第 (c) 項;`belongs_to: "R7"` 掛回父;qualifier 被填到 `condition`/`timing`):
```json
{
  "clause_id": "R7_c",
  "belongs_to": "R7",
  "parent": null,
  "logic_type": null,
  "actor": null,
  "action": "include",
  "object": "restore the availability and access to personal data in a timely manner in the event of a physical or technical incident",
  "recipient": null,
  "modality": "can",
  "condition": "in the event of a physical or technical incident",
  "timing": "in a timely manner",
  "manner": null, "target": null, "location": null, "cause": null,
  "obligation_groups": ["Security Of Processing"],
  "core_sentence": "The organizational and technical measures to ensure a level of security include restore the availability and access to personal data in a timely manner in the event of a physical or technical incident.",
  "source_text": "The organizational and technical measures to ensure a level of security can include: (a) ... (c) restore the availability and access to personal data in a timely manner in the event of a physical or technical incident, ... (Art. 32(1))"
}
```
(R7 共有 `R7_a` `R7_b` `R7_c` `R7_d` 四個子,對應 (a)~(d)。)

### 1b. 合約 norm 範例(Online124 main_5.7.2:資料外洩登錄簿;父 + 一個子)
來源:`results/Online124/run1/system/norms/Online124_norms.json`

父 `main_5.7.2_main`:
```json
{
  "clause_id": "main_5.7.2_main",
  "parent": null,
  "actor": "The Data Processor",
  "action": "have and maintain",
  "object": "a register of all Personal Data Breaches",
  "recipient": null,
  "modality": "shall",
  "condition": null, "manner": null,
  "obligation_groups": ["Personal Data Breach Management"],
  "core_sentence": "The Data Processor shall have and maintain a register of all Personal Data Breaches.",
  "source_text": "The Data Processor shall have and maintain a register of all Personal Data Breaches. The register shall at a minimum include the following:"
}
```

子 `main_5.7.2_i`(`parent: "main_5.7.2_main"`;列舉項;`modality: "plain statement"` 但因父為 shall 仍視為強制):
```json
{
  "clause_id": "main_5.7.2_i",
  "parent": "main_5.7.2_main",
  "actor": null,
  "action": "include",
  "object": "a description of the nature of the Personal Data Breach, including, if possible, the categories and the approximate number of affected Data Subjects and the categories and the approximate number of affected registrations of personal data",
  "recipient": null,
  "modality": "plain statement",
  "condition": null,
  "manner": "including, if possible",
  "obligation_groups": ["Personal Data Breach Management"],
  "core_sentence": "The a register of all Personal Data Breaches include a description of the nature of the Personal Data Breach, including, if possible, the categories and the approximate number of affected Data Subjects and the categories and the approximate number of affected registrations of personal data.",
  "source_text": "A description of the nature of the Personal Data Breach, including, if possible, the categories and the approximate number of affected Data Subjects and the categories and the approximate number of affected registrations of personal data."
}
```

---

## 2. 6c 推理範例 —— 一個完整的 reg–contract 配對怎麼判

6c 是「方向性蘊涵」判斷:合約義務必須**達到或超過**法規要求才算 Compliant。輸入是雙方的結構化欄位
(原文作輔助),輸出是一個 verdict + 四欄理由。下例為 **R16 ↔ Online124 main_5.2.1**(6b cosine 0.714)。
來源:`results/Online124/run1/system/compliance_results/stage_c_results.json`(+ 兩端 norm 取自 norms/)。

**輸入 — 法規 R16(Art. 28(3)(c) 安全措施):**
```json
{ "clause_id": "R16", "actor": "The processor", "action": "take",
  "object": "all measures required pursuant to Article 32 or to ensure the security of processing",
  "modality": "shall",
  "source_text": "The processor shall take all measures required pursuant to Article 32 or to ensure the security of processing. (Art. 28(3c))" }
```

**輸入 — 合約 main_5.2.1:**
```json
{ "clause_id": "main_5.2.1", "actor": "The Data Processor", "action": "implement",
  "object": "the appropriate technical and organizational measures as set out in this Agreement and in the Applicable Law, including in accordance with GDPR, article 32",
  "modality": "shall",
  "manner": "as set out in this Agreement and in the Applicable Law, including in accordance with GDPR, article 32",
  "source_text": "The Data Processor shall implement the appropriate technical and organizational measures as set out in this Agreement and in the Applicable Law, including in accordance with GDPR, article 32." }
```

**輸出 — 6c 判定:**
```json
{
  "similarity": 0.714,
  "verdict": "Compliant",
  "core_alignment_check": "actor/action/object align: processor shall take/implement all required security measures under Article 32; contract requires appropriate technical and organizational measures in accordance with GDPR Article 32, which is the same substantive security obligation and at least as specific",
  "condition_check": "No condition on either side; compatible",
  "modality_check": "shall matches shall; equal force",
  "other_constraints_check": "Contract is at least as strict: 'appropriate technical and organizational measures' in accordance with Article 32 and applicable law meets/exceeds 'all measures required pursuant to Article 32 or to ensure security of processing'"
}
```
verdict 三選一:`Compliant`(滿足)/ `Violation`(有對到但較鬆或矛盾)/ `Gap`(根本沒對到)。

### 2b. 結構化欄位「立功」的正確拒絕 —— R10 ↔ Online39 10.3(「禁止/授權」vs「通知」)
兩條文表面很近(都在講次處理者 + 授權),6b cosine 也高,但**關鍵結構化欄位 `action` / `modality` 不一樣**,
而這正是正確拒絕的原因:
- 法規 R10 的 `action` = **engage**、`modality` = **shall not** —— 是「**未經事前授權,不得委任**次處理者」的**禁止/授權關卡**。
- 合約 10.3 的 `action` = **inform** —— 只是「**告知**控制者次處理者的異動」的**通知義務**。

把結構化欄位並排,一眼看出「`shall not engage`(禁止)≠ `shall inform`(通知)」,**只用結構化欄位就正確判 Gap**;
但**只用原文時,被兩邊共有的「sub-processor / authorization」字面帶過、誤判 Compliant**。確認是正確拒絕:
GT 中 10.3 是 **R11 / R36(通知義務 / 控制者反對權)** 的對應句、**不是** R10 的,故拒絕才對。
跨 3 次 run(見 `results_ablation/ABLATION_REPORT.md`):**structured_only = Gap(3/3)**、**text_only = Compliant(3/3)**、主系統一致拒絕(Violation/Gap)。

**輸入 — 法規 R10:** `{ "actor": "The processor", "action": "engage", "object": "a sub-processor", "modality": "shall not", "condition": "without a prior specific or general written authorization of the controller" }`
原文:`The processor shall not engage a sub-processor without a prior specific or general written authorization of the controller. (Art. 28(2))`

**輸入 — 合約 10.3:** `{ "actor": "the Processor", "action": "inform", "object": "the Controller of any intended changes concerning the addition or replacement of Sub-processors", "modality": "shall", "condition": "In the case of general written authorisation" }`
原文:`In the case of general written authorisation, the Processor shall inform the Controller of any intended changes concerning the addition or replacement of Sub-processors … If the Controller does not object … within 30 days … such changes shall be deemed to be accepted …`

**只用結構化欄位(structured_only)→ Gap(正確拒絕,理由直指 `action`/`modality`):**
```json
{
  "verdict": "Gap",
  "core_alignment_check": "No: the regulation prohibits the processor from engaging a sub-processor without prior specific or general written authorization; the contract only requires the processor to inform the controller of intended changes concerning addition/replacement of sub-processors, which is a notice duty and does not itself require authorization before engagement.",
  "modality_check": "Weaker/different: 'shall inform' is not equivalent to 'shall not engage' and does not impose the substantive prohibition."
}
```

**對照 — 只用原文(text_only)→ Compliant(誤判,被字面相似帶過):**
```json
{
  "verdict": "Compliant",
  "core_alignment_check": "processor/sub-processor engagement: yes, both concern adding/replacing sub-processors and controller authorization",
  "modality_check": "equal-or-stronger: contract requires notification and objection mechanism before changes take effect, which is at least as strict as authorization"
}
```

### 2c. verdict = "Gap" 範例(同領域、但不同義務)—— R15 ↔ Online124 main_5.4
來源:`results/Online124/run1/system/...stage_c_results.json`。R15 要求「確保獲授權人員**承諾保密**」;合約 main_5.4 是「確保員工**只依指示**處理」—— 同一行為人(處理者)、同一大主題(員工管控),但 object 是不同義務 → 判 Gap(體現「同主題≠同義務」)。

**輸入 — 法規 R15:** `{ "actor": "The processor", "action": "ensure", "object": "that persons authorized to process personal data have committed themselves to confidentiality or are under an appropriate statutory obligation of confidentiality", "modality": "shall" }`
原文:`The processor shall ensure that persons authorized to process personal data have committed themselves to confidentiality or are under an appropriate statutory obligation of confidentiality. (Art. 28(3b))`

**輸入 — 合約 main_5.4:** `{ "actor": "The Data Processor", "action": "ensure", "object": "that the Data Processor's employees working processing the Personal Data only processes the Personal Data in accordance with the Instruction", "modality": "shall" }`

**輸出 — 6c 判定:**
```json
{
  "verdict": "Gap",
  "core_alignment_check": "actor/action/object: same actor (processor) and same general duty to ensure employee conduct, but the object is different; the contract requires employees to process only in accordance with instructions, not that authorized persons commit to confidentiality or are under statutory confidentiality obligations",
  "condition_check": "No condition in either side",
  "modality_check": "shall matches shall",
  "other_constraints_check": "No relevant timing/manner/target/location/cause differences"
}
```

### 2d. 法規 actor 是「控制者自身」→ 系統正確判 Gap —— R43 ↔ Online39 5.4
來源:`results/Online39/run1/system/...stage_c_results.json`。R43 的 actor 是 **the controller**、是控制者**自己**該做的事(DPIA 時徵詢 DPO);合約 5.4 是處理者**協助**控制者做 DPIA。6c 的「party-role direction」原則正確指出:這是控制者自身義務、不是處理者協助能滿足的同一義務 → Gap。(對照:R17/R22 等「處理者協助」型規則,同樣的協助條款會判 Compliant;差別在法規本身是不是處理者的義務。)

**輸入 — 法規 R43:** `{ "actor": "The controller", "action": "seek advice of", "object": "the DPO", "modality": "shall", "condition": "when carrying DPIA" }`
原文:`The controller shall seek advice of the DPO when carrying DPIA. (Art. 35(2))`

**輸入 — 合約 5.4:** `{ "actor": "the Processor", "action": "assist", "object": "the Controller in its compliance with an obligation to carry out a DPIA and prior consulting of supervisory authorities", "recipient": "the Controller", "condition": "where necessary and taking into account the nature of the Processing" }`

**輸出 — 6c 判定:**
```json
{
  "verdict": "Gap",
  "core_alignment_check": "No: the regulation requires the controller itself to seek advice of the DPO when carrying out a DPIA, while the contract imposes a processor assistance duty to help the controller comply with DPIA and prior consultation obligations; that is a different, assistance-based obligation and does not specifically require seeking advice of the DPO.",
  "condition_check": "Compatible in part: both are triggered in the DPIA context, though the contract adds a necessity/nature qualifier.",
  "modality_check": "Compatible: the contract uses a mandatory assistance obligation, not a mere permission.",
  "other_constraints_check": "Compatible: the contract's assistance duty is at least as narrow as the regulatory context and does not weaken timing or other constraints."
}
```

---

## 3. Obligation group taxonomy —— 16 類(本次分類器產出)

由 obligation classifier(LLM)針對 GDPR_DPA_Requirements 產生的義務群組;6a 用「reg 與 con norm 是否共享群組」
做交集過濾。本次共 **16 類**(全部都有 reg norm 落入)。
來源:`results/Online124/run1/system/obligation_groups/GDPR_DPA_Requirements_obligation_groups.json`(`categories`)。

| # | 群組名稱 | 定義(節錄) |
|---|---|---|
| 1 | DPA Parties And Processing Scope | 識別締約方、描述基本處理安排(身分/聯絡、期間、性質與目的、資料類型、資料主體類別) |
| 2 | Security Of Processing | 技術與組織保護措施、風險評估、機密性控制,含安全相關協助 |
| 3 | Personal Data Breach Management | 外洩偵測/通報/記錄/通知內容、向主管機關或資料主體通報,含協助通報 |
| 4 | Data Protection Impact Assessment | 進行/支援/記錄/檢視/諮詢 DPIA 及其內容與利害關係人意見 |
| 5 | Sub-Processor Authorization And Oversight | 次處理者的委任/變更/異議/契約義務傳遞,含相關責任與授權 |
| 6 | Processing Instructions And Legal Basis | 僅依書面指示處理、法律要求無指示處理之情形、通報違法指示;含授權人員之指示遵循 |
| 7 | Confidentiality Of Personnel | 確保獲授權處理者受保密承諾或法定保密義務拘束(僅限人員保密) |
| 8 | Assistance With Data Subject And Regulatory Rights | 協助控制者處理資料主體權利請求、主管機關諮詢等(非外洩/安全類)合規行政支援 |
| 9 | Data Return Deletion And Post-Termination Handling | 服務結束時個資的返還、刪除與後續處置(僅限服務終止後處置) |
| 10 | Compliance Evidence Audit And Accountability | 提供合規資訊、允許稽核/查核、證明遵循處理者義務(問責證據與稽核配合) |
| 11 | International Transfer Restrictions | 限制向第三國/國際組織傳輸,除非控制者授權(僅限跨境傳輸授權) |
| 12 | Codes Of Conduct And Certification | 以核可的行為準則或驗證機制證明遵循(作為保證證據的符合機制) |
| 13 | Controller Oversight And Remedial Rights | 控制者保留的監督/補救權:對次處理者變更異議、暫停處理、終止 DPA |
| 14 | Controller Breach Notification And Documentation | 控制者向主管機關通報外洩、記錄外洩、向資料主體通報高風險外洩(僅限控制者外洩義務) |
| 15 | Controller DPIA And Consultation Duties | 控制者執行 DPIA、徵詢 DPO、徵詢資料主體意見、風險變動時檢視(僅限控制者 DPIA 義務) |
| 16 | Liability For Processing Damage | 處理造成損害的責任分配(含處理者不遵循之責任、控制者違規處理之責任) |

(完整英文定義見來源 JSON 的 `categories[].definition`。)

---

## 4. Funnel —— 每階段進出數量(兩份各一,run1)

來源:`results/<contract>/run1/system/analysis/funnel.json`。三階段層層過濾;6a 輸入 = reg norms × con norms 全配對。

「本關篩除率」= 該關篩除 ÷ 該關輸入;「占初始總配對」= 該關篩除 ÷ 該 run 的 6a 輸入配對。

### Online124(reg 58 × con 95)
| 階段 | 輸入配對 | 通過 | 篩除 | 本關篩除率 | 占初始總配對 |
|---|---|---|---|---|---|
| 6a 群組過濾(交集非空) | 5 510 | 770 | 4 740 | 86.0% | 86.0% |
| 6b cosine ≥ 0.40 | 770 | 537 | 233 | 30.3% | 4.2% |
| 6c 推理(Compliant / Violation / Gap = 60 / 6 / 471) | 537 | 60 | 477 | 88.8% | 8.7% |
| 聚合後預測(去重 rule×origin) | — | 43 | — | — | — |

### Online39(reg 58 × con 179)
| 階段 | 輸入配對 | 通過 | 篩除 | 本關篩除率 | 占初始總配對 |
|---|---|---|---|---|---|
| 6a 群組過濾(交集非空) | 10 382 | 1 551 | 8 831 | 85.1% | 85.1% |
| 6b cosine ≥ 0.40 | 1 551 | 777 | 774 | 49.9% | 7.5% |
| 6c 推理(Compliant / Violation / Gap = 102 / 17 / 658) | 777 | 102 | 675 | 86.9% | 6.5% |
| 聚合後預測(去重 rule×origin) | — | 69 | — | — | — |

(三次 run 的平均與全展開見 `results/FINAL_REPORT.md` §3/§4/§6。)

---

## 5. 其他可參考素材

### 5a. 資料規模
- 法規 `GDPR_DPA_Requirements`:58 條 norm,其中 **46 條為頂層 rule**(R1–R46;其餘為列舉子 norm)。
- `Online124`:95 條合約 norm;GT 共 18 條 rule 有對應句、**28 句 Full**(評估分母)。
- `Online39`:179 條合約 norm;GT 共 30 條 rule 有對應句、**58 句 Full**(評估分母)。
  來源:`norms/*.json`、`evaluation/gt/online{124,39}_ground_truth.csv`、`FINAL_REPORT.md`(TP+FN)。

### 5b. 三階段比對(6a→6b→6c)
- **6a 義務群組過濾**:reg 與 con norm 至少共享一個 obligation group 才保留 → 砍掉跨主題配對。
- **6b core_sentence cosine**:用 `core_sentence` 的 OpenAI `text-embedding-3-large` 向量算 cosine,≥ threshold(0.40)才進 6c。
- **6c LLM 方向性蘊涵**:見 §2;輸出 Compliant / Violation / Gap。
- **聚合**:把 Compliant 配對收斂到 rule 層級(`belongs_to` 非空用之,否則 clause_id),以 (rule, origin clause) 去重 → `compliant.csv`。
- **評估**:逐句 containment —— 某 GT Full 句若(normalize 後)是某 Compliant 配對合約 `source_text` 的子字串即命中(TP)。

### 5c. 主結果一句話(完整見 FINAL_REPORT.md)
- 三次 run、threshold 0.40。系統 mean F1:Online124 ≈ 0.667、Online39 ≈ 0.558。
- 兩份合算(§1):主系統 macro F1 ≈ 0.612 / micro F1 ≈ 0.598。各 method 的 P/R/F1、TP/FP/FN、成本、時間
  與每次 run 全展開見 `results/FINAL_REPORT.md`。

### 5d. 一句話對多規則(需求中心的示例)
Online39 中「`The Processor agrees to implement appropriate technical and organisational measures …`」一句
同時對應 R16 / R32 兩條規則 —— 需求中心的設計能讓同一句被多條規則各自命中(段落分類 baseline 通常只給一個標籤)。

### 5e. baseline 對照(同一份輸入、同 threshold 0.40)
- naive LLM:整份合約 + 單一規則丟給 LLM 直接判。
- Traditional RAG:檢索 + LLM judge。
- Dense Retrieval:純檢索(無生成);門檻 0.40 下會回很多候選 → 精確率偏低。
- Passage classification:逐句 zero-shot 分類、段落當 context。
  各 method 三次的 P/R/F1/成本/時間見 `FINAL_REPORT.md`。

### 5f. 6c-only ablation(只換 6c 輸入型態,其餘重用 canonical;n=3、threshold 0.40)
控制變因:四變體**共用同一段實質判準**,只差「給模型看什麼輸入 / 以何者為主」。F1 mean(完整 P/R/F1/TP/FP/FN 見 `results_ablation/ABLATION_REPORT.md`):

| 變體 | 輸入 | Online124 F1 | Online39 F1 |
|---|---|---|---|
| **main**(現行) | 結構化為主 + 原文為輔 | **0.667** | 0.558 |
| structured_only | 只給結構化欄位 | 0.583 | 0.506 |
| text_only | 只給原文 | 0.550 | 0.549 |
| hybrid_textprimary | 原文為主 + 結構化為輔 | 0.609 | 0.563 |

觀察(只列數據,不下定論):兩份上「兩種輸入都給」(main / hybrid)的 F1 都高於「只給一種」(structured_only / text_only);
Online124 上「結構化為主」(main 0.667)明顯高於「原文為主」(hybrid 0.609),Online39 上兩者相近(0.558 vs 0.563)。
prompt 在 `src_ablation/ablation_prompts.py`(共用判準段由現行 main 6c 即時擷取,確保只差輸入)。
