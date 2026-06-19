"""
gen_runtime.py — 生成 LLM 後端切換(OpenAI / ollama),介面對齊原本 chat 呼叫。

設計目標:不改原本 OpenAI 呼叫的「行為」。不設任何 env 時,完全等同原本
(OpenAI + 各檔自己的 LLM_MODEL + 各檔自己的 reasoning_effort),所以原本 5.4-mini
跑出來的結果不變。要做 model swap 實驗時,driver 用 env 覆蓋:
  - GEN_BACKEND = openai | ollama
  - GEN_MODEL   = 生成模型字串(覆蓋各檔的 LLM_MODEL)

ollama 走 OpenAI 相容端點(base_url + /v1)+ HTTP Basic Auth(從 config.yaml 讀)。
ollama 不吃 reasoning_effort,呼叫時自動略過。

成本/時間:每次呼叫後把 usage(實際 token,含 cached)交給 cost_meter,並把這次的
耗時(秒)也記給 cost_meter(add_elapsed)。cost_meter 會寫到 env COST_DIR 指定的資料夾
(driver 設成各模型資料夾),所以實驗成本/時間獨立記錄,不覆蓋原本。
"""

import os
import re
import time
import base64
from pathlib import Path
import yaml
from openai import OpenAI

import cost_meter


def _extract_json(s: str) -> str:
    """local 模型沒有強制 json 格式,可能夾 markdown fence 或 <think> 前言;
    取第一個 { 到最後一個 } 之間的子字串當 JSON(openai 端內容本來就是純 JSON,無影響)。"""
    if not isinstance(s, str):
        return s
    m = re.search(r"\{.*\}", s, re.DOTALL)
    return m.group(0) if m else s

# 本檔位於 release/lib/,config.yaml 在上一層(release 根)。可用 env GEN_CONFIG 覆寫。
ROOT = Path(__file__).resolve().parent
CONFIG_PATH = Path(os.environ.get("GEN_CONFIG") or (ROOT.parent / "config.yaml"))
_CFG = None

# ======================================================================
# REASONING EFFORT(OpenAI gpt-5.4 系列適用)—— 唯一的全域開關。
# 各生成腳本的 REASONING_EFFORT 常數預設為 None(= 繼承這裡),會被當成「沒指定」傳進
# chat(),於是 fallback 到本常數。所以要全域改動,只改這一個值即可(exp18-19 用 "none")。
# per-step 覆寫:把某腳本的 REASONING_EFFORT 設成非 None 值,該值只蓋過那一步。
# 可選值(OpenAI):"none" / "low" / "medium" / "high"。
# ollama(local)不吃此參數,走 think=False,與 "none" 公平對齊。
# ======================================================================
DEFAULT_REASONING_EFFORT = "none"


def _cfg():
    global _CFG
    if _CFG is None:
        # 沒有 config.yaml 時不直接崩潰:openai 路徑只需 OPENAI_API_KEY 即可獨立跑 baseline;
        # 只有 ollama 路徑才真的需要 config(會在 build_client 給清楚的錯)。
        if CONFIG_PATH.exists():
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                _CFG = yaml.safe_load(f) or {}
        else:
            _CFG = {}
    return _CFG


def backend() -> str:
    return os.environ.get("GEN_BACKEND") or _cfg().get("generation", {}).get("backend", "openai")


def build_client(timeout: int = 60):
    """依 backend 建立 client。openai = 原本;ollama = 相容端點 + Basic Auth。"""
    if backend() == "ollama":
        oc = _cfg().get("ollama")
        if not oc:
            raise RuntimeError(
                "backend=ollama 需要 config.yaml 的 ollama 區塊。\n"
                "請複製設定:Copy-Item config.example.yaml config.yaml 並填好 ollama(見 SETUP.md)。")
        auth = base64.b64encode(f"{oc['account']}:{oc['password']}".encode()).decode()
        # local 模型很慢(27b/31b 一次生成可能數分鐘),用大 timeout 蓋過各檔的小 timeout,
        # 否則 classifier/抽取/6c 會 APITimeoutError。可由 config ollama.timeout 調。
        ol_timeout = oc.get("timeout", 1800)
        return OpenAI(
            base_url=oc["base_url"].rstrip("/") + "/v1",
            api_key="ollama",
            default_headers={"Authorization": "Basic " + auth},
            timeout=ol_timeout,
        )
    return OpenAI(api_key=os.getenv("OPENAI_API_KEY"), timeout=timeout)


def resolve_model(default_model: str) -> str:
    return os.environ.get("GEN_MODEL") or default_model


def chat(client, model: str, messages, temperature: float,
         response_format=None, reasoning_effort=None) -> str:
    """
    對齊原本 client.chat.completions.create(...) 的呼叫,回傳 message.content 字串。
    - model 會被 env GEN_MODEL 覆蓋。
    - reasoning_effort 只在 openai backend 傳遞(ollama 不支援,自動略過)。
    - 量測耗時並把 usage + 耗時記給 cost_meter。
    """
    bk = backend()
    use_model = resolve_model(model)
    kw = dict(model=use_model, messages=messages, temperature=temperature)
    if bk == "openai":
        # OpenAI:含 response_format + reasoning_effort。
        if response_format is not None:
            kw["response_format"] = response_format
        # reasoning_effort:呼叫端沒帶(None)時 fallback 到 DEFAULT_REASONING_EFFORT(= "none"),
        # 保證即使某處忘了帶,OpenAI 端也一定鎖在 none(與 exp18-19 一致)。
        eff = reasoning_effort if reasoning_effort is not None else DEFAULT_REASONING_EFFORT
        if eff is not None:
            kw["reasoning_effort"] = eff
    else:
        # ollama:
        #  - 不傳 response_format(json grammar-constrained 解碼會讓 server 回 500 / 卡住);
        #  - 不傳 reasoning_effort(不支援);
        #  - think=False 關掉 thinking,跟 OpenAI 的 reasoning_effort="none" 公平對齊
        #    (凸顯架構而非模型 thinking 扛任務;_extract_json 仍當去 <think> 的安全網)。
        kw["extra_body"] = {"think": False}

    t0 = time.time()
    resp = client.chat.completions.create(**kw)
    dt = time.time() - t0

    cost_meter.add_chat(resp)
    cost_meter.add_elapsed(dt, model=use_model, backend=bk)
    content = resp.choices[0].message.content
    if bk == "ollama":
        content = _extract_json(content)
    return content
