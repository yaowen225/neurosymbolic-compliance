# results/ — canonical 實驗輸出

由 `run_canonical.py` 產生。每 run 用一個**全新且唯一**的 Neo4j KG database(記在 `run_meta.json`)。
進度與續跑狀態見 `RUN_STATE.md`(人看)/ `.run_state.json`(機器讀);每 run 各 step 完成會在
`<run>/.ckpt/<step>.done` 留 sentinel,重跑時已完成的 step 直接跳過(不重算、不重複計費)。

統計報告:`FINAL_REPORT.md`(由 `build_final_report.py` 讀以下原始輸出計算;含 provenance)。

## 結構
```
results/
├── README.md
├── FINAL_REPORT.md              # 各 contract×method 的 3 次 P/R/F1/TP/FP/FN(mean+range)、funnel、$、時間 + provenance
├── RUN_STATE.md / .run_state.json
├── Online124/  和  Online39/
│   ├── run1/  run2/  run3/      # 三次;各自獨立、同結構、用不同 KG db
│   │   ├── run_meta.json        # model/backend/temp/threshold/kg_db/timestamp/run#
│   │   ├── .ckpt/               # 各 step 的完成 sentinel(續跑用)
│   │   ├── system/
│   │   │   ├── parsed/                  # 法規 + 合約 parsed json
│   │   │   ├── norms/  obligation_groups/  embeddings/
│   │   │   ├── compliance_results/      # stage_a(6a)/stage_b(6b)/stage_c(6c)/compliant.csv/requirement_summary.json
│   │   │   ├── analysis/                # funnel.json(6a/6b/6c 各過濾多少)+ threshold_sensitivity / sentence_diagnosis
│   │   │   ├── eval/                    # evaluation_results_system.csv
│   │   │   ├── cost/                    # main_usage.json(token 用量;$ 在 FINAL_REPORT 算)
│   │   │   └── time/                    # step_times.json(各 step wall-clock)
│   │   └── baselines/
│   │       ├── naive_baseline/    │
│   │       ├── rag_baseline/      │  各含:<method>_compliant.csv、eval/evaluation_results.csv、
│   │       ├── dense_baseline/    │       cost/<method>_usage.json、time/time.json
│   │       └── passage_baseline/  │
```

## 參數(這份結果)
- threshold = 0.40(系統 6b 與 RAG/dense baseline 一律 0.40);temperature = 0。
- 生成 LLM 見各 `run_meta.json`;embedding 一律 OpenAI `text-embedding-3-large`。
- 重跑:從 /release 根目錄執行 `python run_canonical.py --config canonical_mini.yaml`(或 `run_canonical_mini.bat`)。
