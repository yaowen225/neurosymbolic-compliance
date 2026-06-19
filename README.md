# GDPR 對於DPAs的合規驗證系統(Knowledge-Graph + 3-stage matching)

把一份「資料處理合約(DPA)」與 GDPR與之對應的法規 都解析成結構化 norm,存進
Neo4j,再用三階段比對判斷合約是否滿足每條法規要求,最後做逐句 containment 評估。

```
parse → obligation classifier → norm extraction → embedding → Neo4j
      → 6a 義務群組過濾 → 6b core_sentence cosine → 6c LLM 方向性蘊涵
      → 聚合 → compliant.csv → 評估(P / R / F1)
```

- 生成 LLM 可換(OpenAI 或 local ollama);**embedding 一律用 OpenAI `text-embedding-3-large`**。
- 評估資料:`GDPR_DPA_Requirements`(法規)對 `Online124`, `Online39`(合約),ground truth 在
  `evaluation/gt/online124_ground_truth.csv`, `evaluation\gt\online39_ground_truth.csv`




---

## Canonical 實驗(一鍵重現)

`run_canonical.py` 把「兩份合約 × 五方法(主系統 + naive/RAG/dense/passage)× 3 次」整套跑完並
完整存檔到 **`results/`**(每 run 用一個全新且唯一的 Neo4j KG db)。**設定檔驅動、可重跑、可續跑**
(中斷後再執行同一指令即從斷點接續,不重算、不重複計費),進度寫在 `results/RUN_STATE.md`。

```bash
# 從本資料夾(/release)根目錄:
# mini 版(gpt-5.4-mini / OpenAI):
run_canonical_mini.bat                 # Windows 一鍵(自動續跑;UTF-8 繁中)
./run_canonical.sh canonical_mini.yaml # bash/lab
# 或直接:
python runners/run_canonical.py --config canonical_mini.yaml

# 實驗室 gemma 版(ollama;KG db 名稱每台不同,用設定檔或 --kg-dbs 覆寫):
run_canonical_gemma.bat
python runners/run_canonical.py --config canonical_gemma.yaml --kg-dbs db1,db2,db3,db4,db5,db6

# 全部跑完後,計算統計報告:
python runners/build_final_report.py           # -> results/FINAL_REPORT.md(各 method 3 次 P/R/F1/funnel/$/時間 + provenance)
```

# 只換 6c 輸入型態的控制變因 ablation(structured_only / text_only / hybrid_textprimary;重用 canonical 資料、只重跑 6c):
python runners/run_ablation_6c.py            # -> results_ablation/<variant>/...
python runners/build_ablation_report.py      # -> results_ablation/ABLATION_REPORT.md

設定都在 `canonical_mini.yaml` / `canonical_gemma.yaml`(model/backend/threshold/contracts/runs/kg_dbs…),
不寫死。`results/` 結構、`run_meta.json`、funnel 等見 `results/README.md`。

> **`config.yaml` 的 `neo4j.database` 一定要寫成雙引號**:`database: "exp30"`。
> 每個 run 會自動把這行的 db 名稱換成該 run 專屬的 KG db,靠的是只認雙引號的取代規則
> (`(database:\s*")[^"]*(")`)。若寫成**不加引號**(`database: exp30`)或**單引號**(`database: 'exp30'`),
> 自動切換會**失效** —— 所有 run 會共用同一個 db、互相覆蓋。其他欄(uri/username/password)不受影響,
> 但建議一律用雙引號保持一致。

> **封存**:舊的 `output/`、一次性 subset/tuning 工具已搬進 **`_archive/`**(已 gitignore;`results/` 不 gitignore)。
> 主要 runner 是 `run_canonical.py`;`run_pipeline.py`(單次跑一份)、`run_evaluation.py`、`run_analysis.py` 仍保留可用。

---

## 目錄結構

```
release/
├─ runners/               所有入口 script(從根目錄用 python runners/<name>.py 執行)
│   ├─ run_canonical.py      整套 canonical 實驗(設定檔驅動,可續跑)
│   ├─ build_final_report.py 讀 results/ 算統計 -> results/FINAL_REPORT.md
│   ├─ run_pipeline.py       單次跑一份合約到 compliant.csv
│   ├─ run_evaluation.py     效能 + 成本 + 時間
│   ├─ run_analysis.py       三組診斷(threshold sensitivity / 逐句 / funnel)
│   └─ run_all_models.py     批次多模型(薄 wrapper)
├─ run_canonical_mini.bat / run_canonical_gemma.bat / run_canonical.sh   一鍵 wrapper(自動續跑)
├─ canonical_mini.yaml / canonical_gemma.yaml   canonical 實驗設定(mini / gemma)
├─ lib/                   被 import 的共用模組(不要直接執行)
│   ├─ gen_runtime.py        生成後端切換(OpenAI/ollama)+ reasoning effort 鎖定處
│   ├─ cost_meter.py         token/時間計量
│   └─ core_sentence_postproc.py  列舉 child 的 core_sentence 後處理
├─ src/                   主 pipeline(1_parsers … 7_aggregation)
├─ src_ablation/          6c 的兩個 ablation 變體
├─ evaluation/            評分核心
│   ├─ evaluate_retrieval.py        算 P/R/F1
│   ├─ baseline_naiveLLM.py         naive LLM baseline(獨立可執行)
│   ├─ baseline_Traditional_RAG.py  Traditional RAG baseline = retrieve + LLM judge(model-dependent,獨立可執行)
│   ├─ baseline_dense_retrieval.py  Dense Retrieval baseline = 純檢索,無 judge(獨立可執行)
│   ├─ baseline_passage_classification.py  Passage-classification baseline = 逐句 zero-shot 分類、段落當 context(model-dependent,獨立可執行)
│   └─ gt/                          ground truth(online124 / online39)
├─ inputs/                raw/ 原始 txt(step1 輸入)+ parsed_* 已切分 json(--skip-step1 / baseline 用)
├─ results/               canonical 實驗輸出(FINAL_REPORT.md / 各 run / RUN_STATE.md;見 results/README.md)
├─ config.example.yaml / .env.example / requirements.txt
└─ README.md / SETUP.md
```

所有入口 script 都在 `runners/`,從根目錄以 `python runners/<name>.py` 執行;`lib/` 的東西只被 import,不要直接執行。
`_archive/`(舊 output/ 與一次性工具)不入版控。

---

## 安裝

下列指令可在 **Windows PowerShell** 直接逐行貼上跑(每行獨立,不用 `\` 換行):

```powershell
# 1) 環境(Python 3.10+)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt

# 2) 金鑰與設定
Copy-Item .env.example .env                 # 編輯 .env,填 OPENAI_API_KEY
Copy-Item config.example.yaml config.yaml   # 編輯 config.yaml,填 Neo4j 連線(local 模型再填 ollama 區塊)
```

> macOS / Linux(bash):啟用虛擬環境改成 `source .venv/bin/activate`;複製檔用 `cp`。其餘相同。

- **`.env`**:`OPENAI_API_KEY`。embedding 一定要用;走 OpenAI 生成模型時也用這把 key。
- **`config.yaml`**(由 `config.example.yaml` 複製):
  - `neo4j`:`uri` / `username` / `password` / `database`。
  - `pricing`:各模型每百萬 token 單價(`run_evaluation.py` 用來換算 $)。
  - `generation`:預設後端/模型(會被 runner 的 `--gen-backend/--gen-model` 覆寫)。
  - `ollama`:只有 local 模型才需要(見 SETUP.md)。

### Neo4j
裝好 Neo4j 5.x,建立並啟動一個資料庫,連線資訊填進 `config.yaml` 的 `neo4j`。
每個模型用一個乾淨 DB(如 `exp1`、`exp2`…);**runner 不會自動清庫**,重跑前請自行清空。
`run_pipeline.py --db <name>` 會自動把 `config.yaml` 的 `neo4j.database` 改成該名稱。

---

## Reasoning effort(OpenAI gpt-5.4 系列)

- **目前鎖定 `none`**。
- **唯一的全域開關**:`lib/gen_runtime.py` 的常數 `DEFAULT_REASONING_EFFORT = "none"`。
- 各生成腳本(`src/2_obligation_classifier`、`src/3_extraction`、`src/6_matching/stage_c_reasoning.py`、
  `src_ablation/*`、`evaluation/baseline_naiveLLM.py`)的 `REASONING_EFFORT` 常數**預設為 `None`,代表「繼承全域」**:
  它會被當成「沒指定」傳進 `gen_runtime.chat()`,於是 fallback 到 `DEFAULT_REASONING_EFFORT`。
  因此這些腳本不是各自的真值來源,改 `DEFAULT_REASONING_EFFORT` 一處就會全域生效。
- **要全域換**:改 `lib/gen_runtime.py` 的 `DEFAULT_REASONING_EFFORT`。
  **要只改某一步(per-step override)**:把該腳本的 `REASONING_EFFORT` 設成非 `None` 值,該值只蓋過那一步。
  可選值:`none` / `low` / `medium` / `high`。
- **適用範圍**:只對 OpenAI gpt-5.4 系列生效。**local / ollama 路徑不吃這參數**,改走 `think=False`
  (與 `reasoning_effort="none"` 公平對齊)。

---

> **跑任何 runner 前,先完成上面「安裝」**——尤其這兩步(在 `release/` 目錄下):
> `Copy-Item config.example.yaml config.yaml`(填 Neo4j / pricing)與
> `Copy-Item .env.example .env`(填 `OPENAI_API_KEY`)。**沒有 `config.yaml` / `.env` 會直接報錯跑不起來。**
> 下面範例的 `--output-dir`(如 `output/runs/gpt-5.4-mini`)是同一個資料夾:先用 `run_pipeline.py` 寫進去,
> 再用同一個路徑給 `run_evaluation.py` / `run_analysis.py`。

## 入口 A — `run_pipeline.py`(跑到 compliant.csv)

把主系統整條跑完(**含 step1 parser**)到產生 `compliant.csv` 為止,並記錄每步 token/時間。**不做評估**。

```powershell
# 預設:跑 step1(parse inputs/raw 的原始 txt)→ … → compliant.csv
python runners/run_pipeline.py --gen-model gpt-5.4-mini --gen-backend openai --db exp1 --output-dir output/runs/gpt-5.4-mini --threshold 0.45

# 跳過 step1,沿用既有 inputs/parsed_*(不想每次重 parse 時)
python runners/run_pipeline.py --gen-model gpt-5.4-mini --gen-backend openai --db exp1 --output-dir output/runs/gpt-5.4-mini --threshold 0.45 --skip-step1

# naive baseline 完全獨立執行
python evaluation/baseline_naiveLLM.py --reg-parsed "inputs/parsed_regulatory/GDPR_DPA_Requirements_parsed.json" --con-parsed "inputs/parsed_contracts/Online124_parsed.json" --out output/runs4/Online124/baselines/naive_baseline/naiveLLM_compliant.csv
```

- **做什麼**:step1 parser(txt→parsed json,**預設會跑**)→ step2 classifier → step3 extract(reg/con,
  含 core_sentence 後處理)→ step4 embedding → step5 KG → 6a → (threshold)→ 6b → 6c reasoning → step7 aggregate。
- **step1**:預設吃 `inputs/raw/` 的原始 txt,parse 到 `<output-dir>/parsed_*`,下游就用這份。
  raw 已驗證 parse 結果與 `inputs/parsed_*` 完全一致。加 `--skip-step1` 則直接沿用
  `inputs/parsed_*`(或自訂 `--reg-parsed/--con-parsed`),不重 parse。
- **輸出到**:`--output-dir`(上例 `output/runs/gpt-5.4-mini/`)底下,**全部落在這裡,不會碰到舊 output/**:
  - `parsed_regulatory/`、`parsed_contracts/`(step1 產物;`--skip-step1` 時不產)
  - `compliance_results/compliant.csv`(最終結果)、`stage_a/b_pairs.json`、`stage_c_results.json`
  - `norms/`、`embeddings/`、`obligation_groups/`、`failures/`
  - `costs/`(`main_usage.json`、`step_times.json`)、`threshold.txt`
- **參數怎麼設(都是 CLI 旗標,無須改檔)**:
  | 旗標 | 意義 | 預設 |
  |---|---|---|
  | `--gen-model` | 生成 LLM | (必填) |
  | `--gen-backend` | `openai` / `ollama` | (必填) |
  | `--db` | Neo4j database 名稱(會寫進 config.yaml) | (必填) |
  | `--output-dir` | 此次輸出資料夾 | (必填) |
  | `--threshold` | 固定 6b threshold;**不給則 autotune** | autotune |
  | `--skip-step1` | 跳過 parser,沿用既有 parsed | 不跳過(會跑 step1) |
  | `--raw-reg` / `--raw-con` | step1 的原始 txt | `inputs/raw/…` |
  | `--reg-parsed` / `--con-parsed` | `--skip-step1` 時的已切分輸入 | `inputs/parsed_*` |
  | `--config` | config.yaml 路徑 | `./config.yaml` |
  | `--gt` | autotune 用的 ground truth | `evaluation/gt/…` |
- threshold 不給時會 autotune(工具 `autotune_threshold.py` 已移到 `_archive/tools/`;canonical 流程一律給固定 threshold,不需要它)。

## 入口 B — `run_evaluation.py`(效能 + 成本 + 時間)

```powershell
# 預設(= --target system naive):評估主系統 + 跑並評估 naive,openai mini
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini

# 只評估主系統
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target system

# 只跑並評估 naive baseline
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target naive

# 只跑並評估 Traditional RAG baseline(retrieve + LLM judge;judge 用 --gen-model)
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target rag

# 只跑並評估 Dense Retrieval baseline(純檢索,無生成 LLM)
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target dense

# 只跑並評估 Passage-classification baseline(逐句 zero-shot 分類、段落當 context)
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-nano --gen-model gpt-5.4-nano --target passage

# 複選:主系統 + RAG + dense + passage(空白分隔,要哪些列哪些)
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target system rag dense passage

# system + naive + rag + dense + passage 全做(all = 全部簡寫)
python runners/run_evaluation.py --output-dir output/runs/gpt-5.4-mini --gen-model gpt-5.4-mini --target all

# local 模型(ollama):judge 走 local 跑 RAG
python runners/run_evaluation.py --output-dir output/runs/gemma4-31b --gen-model gemma4:31b --gen-backend ollama --target rag
```

- **做什麼**:對 `--output-dir` 的 `compliant.csv` 算 Precision/Recall/F1;再從 `costs/` 算成本
  (OpenAI 以 $,local 以 token)與時間。**主系統與各 baseline 解耦**。
- **`--target`(可複選,空白分隔,列出要哪些)**:
  - `system`:只評估主系統。
  - `naive`:只跑+評估 naive LLM baseline(只需切分後輸入,不依賴 KG pipeline)。
  - `rag`:只跑+評估 **Traditional RAG baseline = retrieve + LLM judge**(檢索取候選後,用 judge LLM
    篩出真正滿足的)。**judge 走 `--gen-model`(GEN_MODEL/GEN_BACKEND),故 RAG 是 model-dependent**;
    成本 = embedding + judge chat。
  - `dense`:只跑+評估 **Dense Retrieval baseline = 純檢索**(top-k cosine,**無生成 LLM**;只有 embedding)。
  - `passage`:只跑+評估 **Passage-classification baseline**(忠實重現論文 paragraph-level 方法:
    **逐句(parser 的每個 clause)一次 zero-shot LLM call,該句所屬頂層章節段落當 context**,單標籤輸出、
    '0'=none;**無 retrieval/embedding**)。**分類走 `--gen-model`,故 model-dependent**;
    規則集 = GDPR_DPA_Requirements R1–R46(與 GT rule 空間一致);句/段皆取自同一份 parsed 合約。
  - `all`:= `system naive rag dense passage` 全做的簡寫。
  - **不給 `--target` 時 = `system naive`**(維持原行為)。可任意組合,如 `--target system rag dense passage`。
- **輸出**:`<output-dir>/REPORT_eval.md`、`evaluation_results_{system,naive,rag,dense,passage}.csv`、
  `naive/naiveLLM_compliant.csv`、`rag/Traditional_RAG_compliant.csv`、`dense/dense_retrieval_compliant.csv`、
  `passage/passage_classification_compliant.csv`(+ `_details.json` 含每段原始回覆與判定;照論文無 justification)。
- **成本各自隔離**:system→`costs/main_usage.json`、naive→`costs/naive_usage.json`、
  rag→`costs/rag_usage.json`(embedding + judge chat 兩步)、dense→`costs/dense_usage.json`(只有 embedding)、
  passage→`costs/passage_usage.json`(只有分類 chat)。互不覆蓋。
- 參數:`--output-dir`、`--gen-model`、`--gen-backend`(預設 openai)、`--target`、`--config`、`--gt`、
  `--reg-parsed`、`--con-parsed`(都是 CLI 旗標)。

## 各 baseline 完全獨立執行:
```powershell
python evaluation/baseline_naiveLLM.py --reg-parsed "inputs/parsed_regulatory/GDPR_DPA_Requirements_parsed.json" --con-parsed "inputs/parsed_contracts/Online124_parsed.json" --out output/runs4/Online124/baselines/naive_baseline/naiveLLM_compliant.csv

python evaluation/baseline_Traditional_RAG.py --reg-parsed "inputs/parsed_regulatory/GDPR_DPA_Requirements_parsed.json" --con-parsed "inputs/parsed_contracts/Online124_parsed.json" --out output/runs4/Online124/baselines/rag_baseline/Traditional_RAG_compliant.csv

python evaluation/baseline_dense_retrieval.py --reg-parsed "inputs/parsed_regulatory/GDPR_DPA_Requirements_parsed.json" --con-parsed "inputs/parsed_contracts/Online124_parsed.json" --out output/runs4/Online124/baselines/dense_baseline/dense_retrieval_compliant.csv

python evaluation/baseline_passage_classification.py --con-parsed "inputs/parsed_contracts/Online124_parsed.json" --out output/runs4/Online124/baselines/passage_baseline/passage_classification_compliant.csv
```

## 入口 C — `run_analysis.py`(一次跑完三組診斷)

```powershell
python runners/run_analysis.py --output-dir output/runs/gpt-5.4-mini
```


- **做什麼**(吃同一個輸出資料夾,跑三組事後診斷,只讀既有產物、不重跑 pipeline):
  1. **threshold sensitivity** — 逐句、只看 6a+6b:不同 threshold 下有幾條 GT 句子活得下來。
  2. **sentence-level diagnosis** — 逐句漏斗:每條 GT 句子最終命中否,未命中漏在哪一關
     (RECOVERED 以 compliant.csv 為準,= 正式評估 TP)。
  3. **pipeline diagnostics** — 各關卡進出數量(FUNNEL)+ FN 清單 + FP 清單。
- **讀什麼**:`<output-dir>` 底下的 `embeddings/`、`compliance_results/{stage_a,stage_b,stage_c}…`、
  `compliance_results/compliant.csv`(都是 `run_pipeline.py` 的產物);GT 用 `--gt`(預設 `evaluation/gt/…`)。
- **要設定的只有 `--output-dir`**。不需要改檔。
- **輸出**:`<output-dir>/analysis/`(`threshold_sensitivity.csv`、`threshold_sensitivity_detail.csv`、
  `sentence_level_diagnosis.csv`、`analysis_report.txt`)。

## 入口 — `run_all_models.py`(批次多模型)

一次把多個模型各自跑完 pipeline(+ 選擇性評估)。本檔是薄 wrapper,沒有流程邏輯,只是轉呼叫上面的 runner。

```powershell
python runners/run_all_models.py                          # 跑 pipeline + 評估(預設 target = system naive)
python runners/run_all_models.py --eval-target all          # 評估改成 system naive rag dense
python runners/run_all_models.py --eval-target system rag   # 評估改成 system + rag(可複選)
python runners/run_all_models.py --no-eval                 # 只跑 pipeline,不評估
python runners/run_all_models.py --output-base output/runs2       # 換輸出根資料夾
```

- **要跑哪些 model 在哪設定**:編輯 **`run_all_models.py` 最上方的 `MODELS` 清單**,每筆是一個 dict:
  ```python
  MODELS = [
      {"model_dir": "gpt-5.4-mini", "gen_model": "gpt-5.4-mini", "backend": "openai", "db": "exp1", "threshold": "0.45"},
      {"model_dir": "gpt-5.4-nano", "gen_model": "gpt-5.4-nano", "backend": "openai", "db": "exp2", "threshold": "0.45"},
      # {"model_dir": "gemma4-31b", "gen_model": "gemma4:31b", "backend": "ollama", "db": "exp3", "threshold": None},
  ]
  ```
  `threshold` 給值(字串或數字皆可)=固定;`None`=autotune。各模型用不同 `db`(請先自行清空)。
- **評估跑哪些 target**:`--eval-target`(透傳給 `run_evaluation.py` 的 `--target`,可複選;
  選項同上:`system` / `naive` / `rag` / `dense` / `passage` / `all`)。預設 `system naive`(維持原本批次行為)。
- **做什麼/輸出**:對每個模型呼叫 `run_pipeline.py`(輸出到 `<output-base>/<model_dir>/`),
  非 `--no-eval` 時再呼叫 `run_evaluation.py`。進度寫到 `<output-base>/PROGRESS.log`;
  pipeline 或評估任一步失敗都會記 `... FAILED` 並跳過該模型,不會誤記成 `MODEL DONE`。
- 背景長跑(local 模型)做法見 SETUP.md。

---

## Ablation 變體(6c 的兩個對照)

主系統 6c(`src/6_matching/stage_c_reasoning.py`)= 結構化欄位為主、原文為輔。
兩個 ablation 在 `src_ablation/`(只在 gpt-5.4-mini 上做對照):

- `stage_c_reasoning_structured_only.py` —— 只用結構化欄位(輸出 `compliance_results/variant_structured_only/`)
- `stage_c_reasoning_text_only.py` —— 只用原文(輸出 `compliance_results/variant_text_only/`)

跑法:先用入口 A 跑到 6b,再手動執行該變體腳本,`--input` 指到 `<out>/compliance_results/stage_b_pairs.json`、
`--output-dir` 指到對應 `variant_*` 路徑;最後用 `evaluation/evaluate_retrieval.py` 或 `run_evaluation.py`
對該變體的 `compliant.csv` 評估。成本記在各自的 `variant_*_usage.json`,不與主系統(`main_usage.json`)混。

## 輸入資料

```
inputs/
├─ raw/                       原始 txt(step1 的輸入)
│   ├─ regulatory/GDPR_DPA_Requirements.txt
│   └─ contracts/Online124.txt、Online39.txt
├─ parsed_regulatory/GDPR_DPA_Requirements_parsed.json          已切分(--skip-step1 / baseline 用)
└─ parsed_contracts/Online124_parsed.json、Online39_parsed.json
```

- 預設 `run_pipeline.py` 會跑 step1:`src/1_parsers/` 把 `inputs/raw/` 的 txt 切成 parsed json
  (輸出到 `<output-dir>/parsed_*`)。canonical 實驗(`runners/run_canonical.py`)一律當場 parse,不吃 `inputs/parsed_*`。
- `inputs/parsed_*` 保留作為 `--skip-step1` 的來源,**也是 baseline 單獨執行時固定吃的輸入**。
