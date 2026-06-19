"""
cost_meter.py — 累計 OpenAI API 實際 token 用量(供成本計算)。

用法(在任何呼叫 OpenAI 的 script 內):
    import cost_meter
    cost_meter.configure(system="main", step="step3_extract_regulatory")
    ...
    resp = client.chat.completions.create(...)
    cost_meter.add_chat(resp)            # 每次 chat 回應後
    ...
    emb = client.embeddings.create(...)
    cost_meter.add_embedding(emb)        # 每次 embedding 回應後
    ...
    cost_meter.flush()                   # script 結束前寫檔

設計:
- token 數一律取自 API 回應的 usage 欄位(實際數,不用 tiktoken 估)。
- chat 的 usage 會區分一般 input 與 cached input
  (usage.prompt_tokens_details.cached_tokens);uncached = prompt_tokens - cached。
- 每個 system 一份 usage 檔:output/costs/<system>_usage.json,以 step 為 key。
  flush() 會「覆寫該 step 的 entry」(重跑某步只更新該步,不重複累加)。
- 成本換算在 cost_report.py(讀 config.yaml 的 pricing)。
"""

import os
import json
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent

_state = {}
_lock = threading.Lock()   # 保護 _state 的並行累加(例如 6c 多執行緒)


def _reset(system, step):
    _state.clear()
    _state.update(
        system=system, step=step,
        chat_prompt=0, chat_cached=0, chat_completion=0, n_chat=0,
        embed_tokens=0, n_embed=0,
        elapsed_s=0.0, model=None, backend=None,
    )


def _out_dir():
    """輸出目錄:env COST_DIR 優先(runner 設成各模型 costs 資料夾),否則預設 <release>/output/costs。
    本檔位於 release/lib/,故預設取 ROOT.parent。"""
    d = os.environ.get("COST_DIR")
    return Path(d) if d else (ROOT.parent / "output" / "costs")


def add_elapsed(dt: float, model=None, backend=None):
    """累加一次 LLM 呼叫的耗時(秒);順便記下 model/backend(供 local 模型成本/時間報告)。"""
    with _lock:
        _state["elapsed_s"] = _state.get("elapsed_s", 0.0) + dt
        if model:
            _state["model"] = model
        if backend:
            _state["backend"] = backend


def configure(system: str, step: str):
    _reset(system, step)


def add_chat(response):
    """累加一次 chat 回應的 usage。"""
    u = getattr(response, "usage", None)
    if u is None:
        return
    pt = getattr(u, "prompt_tokens", 0) or 0
    ct = getattr(u, "completion_tokens", 0) or 0
    cached = 0
    details = getattr(u, "prompt_tokens_details", None)
    if details is not None:
        cached = getattr(details, "cached_tokens", 0) or 0
    with _lock:
        _state["chat_prompt"] += pt
        _state["chat_cached"] += cached
        _state["chat_completion"] += ct
        _state["n_chat"] += 1


def add_embedding(response):
    """累加一次 embedding 回應的 usage(只有 input)。"""
    u = getattr(response, "usage", None)
    if u is None:
        return
    pt = getattr(u, "prompt_tokens", 0) or getattr(u, "total_tokens", 0) or 0
    with _lock:
        _state["embed_tokens"] += pt
        _state["n_embed"] += 1


def flush():
    """把目前 step 的累計用量寫入 output/costs/<system>_usage.json(覆寫該 step)。"""
    if not _state.get("system"):
        return
    path = _out_dir() / f"{_state['system']}_usage.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    data[_state["step"]] = {
        "chat": {
            "uncached_input": _state["chat_prompt"] - _state["chat_cached"],
            "cached_input": _state["chat_cached"],
            "output": _state["chat_completion"],
            "n_calls": _state["n_chat"],
        },
        "embedding": {
            "input": _state["embed_tokens"],
            "n_calls": _state["n_embed"],
        },
        "elapsed_s": round(_state.get("elapsed_s", 0.0), 3),
        "model": _state.get("model"),
        "backend": _state.get("backend"),
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    print(f"[cost_meter] {_state['system']}/{_state['step']}: "
          f"chat(uncached {data[_state['step']]['chat']['uncached_input']}, "
          f"cached {_state['chat_cached']}, out {_state['chat_completion']}, n {_state['n_chat']}) "
          f"embed({_state['embed_tokens']} tok, n {_state['n_embed']}) -> {path.name}")
