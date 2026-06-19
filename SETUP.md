# SETUP —— 在另一台(實驗室)電腦上跑 local 模型(gemma / qwen)

這份是給「要用 ollama 上的 local 生成模型(如 `gemma4:31b`、`qwen3.5:27b`)跑整套系統」的安裝說明。
與一般用法的唯一差別在生成後端,其餘流程相同。

>  **雙後端(務必看):生成走 ollama,但 embedding 仍走 OpenAI。**
> local 模型只負責「生成」(classifier / norm 抽取 / 6c 推理 / naive baseline / Traditional RAG 的 judge)。
> 向量 **embedding 一律用 OpenAI `text-embedding-3-large`**(`src/4_embedding`),
> 所以這台電腦**即使只跑 local 生成模型,也一定要有可用的 `OPENAI_API_KEY` 和對外網路**,
> 否則 step4 embedding 會失敗。

---

## 1. 要複製什麼

把**整個 `release/` 資料夾**複製到這台電腦即可(它是自足的:程式碼 + 原始/已切分輸入 + 設定範本 + 文件)。
不需要複製實驗輸出(`output/runs/` 等)。

## 2. 要安裝什麼

- **Python 3.10+**
- 虛擬環境 + 套件(**Windows PowerShell**,逐行貼):
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install -r requirements.txt
  ```
  > macOS / Linux(bash):`python -m venv .venv` 後 `source .venv/bin/activate`,再 `pip install -r requirements.txt`。
  > 用 conda:`conda create -n gdpr python=3.10 -y` 然後 `conda activate gdpr`(PowerShell 不支援 `&&`,請分兩行)。

  `requirements.txt` 內含:`openai`、`tenacity`、`python-dotenv`、`numpy`、`scikit-learn`、`neo4j`、`PyYAML`。
- **Neo4j 5.x**(見下方第 4 節)。
- 這台電腦要能連到提供 local 模型的 **ollama 服務**(OpenAI 相容端點)。

## 3. 要設定什麼

```powershell
Copy-Item .env.example .env
Copy-Item config.example.yaml config.yaml
```
> bash:用 `cp .env.example .env` 與 `cp config.example.yaml config.yaml`。

### `.env`
```
OPENAI_API_KEY=sk-...        # embedding 必用;務必填可用的 key
```

### `config.yaml`
- `generation`:設成 local
  ```yaml
  generation:
    backend: ollama
    model: gemma4:31b          # 或 qwen3.5:27b
  ```
  (也可不改這裡,改用 runner 的 `--gen-backend ollama --gen-model gemma4:31b` 覆寫。)
- `ollama`:OpenAI 相容端點 + HTTP Basic Auth
  ```yaml
  ollama:
    base_url: "http://<OLLAMA_HOST>:11434"   # 程式會自動接 /v1
    account:  "<BASIC_AUTH_帳號>"
    password: "<BASIC_AUTH_密碼>"
    timeout:  1800             # local 模型很慢,單次請求 timeout(秒);太小會 APITimeoutError
  ```
- `neo4j`:填本機 Neo4j 連線(見第 4 節)。
- `pricing`:local 模型沒有 $ 定價,評估時會自動改以 token + 時間呈現(embedding 仍計 $)。

> local 模型的細節(都在 `lib/gen_runtime.py`,已內建不用調):程式以 `think=False` 關掉 thinking
> (與 OpenAI `reasoning_effort="none"` 公平對齊);不送 `response_format`(部分 ollama server 的
> JSON grammar 解碼會卡住/回 500),改靠 prompt 要 JSON 再用 `_extract_json` 抽取。
> reasoning effort 只對 OpenAI 生效,ollama 不吃此參數(詳見 README「Reasoning effort」一節)。

## 4. Neo4j

1. 安裝 Neo4j 5.x(Neo4j Desktop 或 server 皆可)。
2. 建立並啟動一個資料庫,記下 uri / 使用者 / 密碼,填進 `config.yaml` 的 `neo4j`。
3. 每個模型用一個乾淨 DB(如 `exp1`/`exp2`);runner 不會自動清庫,重跑前請自行清空。

## 5. 怎麼跑

PowerShell(每行獨立,可直接貼;`--gen-backend ollama` 時 local 生成):

```powershell
# (A) pipeline(local 生成,threshold 不給則 autotune)
python runners/run_pipeline.py --gen-model gemma4:31b --gen-backend ollama --db exp3 --output-dir output/runs/gemma4-31b

# (B) 評估(local 模型會以 token + 時間呈現成本)
python runners/run_evaluation.py --output-dir output/runs/gemma4-31b --gen-model gemma4:31b --gen-backend ollama

# (C) 診斷
python runners/run_analysis.py --output-dir output/runs/gemma4-31b
```

整條 local 跑很久(單次生成可能數分鐘,整條數小時)。建議背景執行:

```powershell
# Windows PowerShell(背景、關掉視窗也續跑;回傳的 .Id 是 PID)
$p = Start-Process python -ArgumentList '-u','run_pipeline.py','--gen-model','gemma4:31b','--gen-backend','ollama','--db','exp3','--output-dir','output/runs/gemma4-31b' -WindowStyle Hidden -PassThru
$p.Id
# 停止: Stop-Process -Id <PID>
```

```bash
# macOS / Linux 背景執行
nohup python runners/run_pipeline.py --gen-model gemma4:31b --gen-backend ollama --db exp3 --output-dir output/runs/gemma4-31b > output/runs/gemma4.log 2>&1 &
```

## 6. 常見問題

- **APITimeoutError**:`config.yaml` 的 `ollama.timeout` 調大(已預設 1800s)。
- **step4 embedding 失敗**:檢查 `OPENAI_API_KEY` 與對外網路(embedding 走 OpenAI,不走 ollama)。
- **ollama 回 500 / 卡住**:確認 `base_url` 正確且該模型已 pull;大型 classifier 請求若該 server 不穩,先用較小模型驗證流程。
