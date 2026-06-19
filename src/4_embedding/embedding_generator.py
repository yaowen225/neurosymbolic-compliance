"""
Step 4: Embedding Generator

只對每條 norm 的 core_sentence 生成單一向量(不再對 actor/action/object 分別生成)。
輸出在每條 norm 上新增 "embedding" 欄位,供 Step 5 寫入 KG。

OpenAI embedding 已是正規化單位向量,後續 cosine 前不需再正規化。

使用方式:
    python embedding_generator.py                       # 預設處理法規 norms
    python embedding_generator.py --input ../../output/norms/Online124_norms.json
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

# 成本計算:累計實際 token 用量
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "lib"))
import cost_meter


# ==================== 實驗參數 (Experimental Parameters) ====================
EMBEDDING_MODEL = "text-embedding-3-large"
MAX_RETRIES = 3

INPUT_PATH = "../../output/norms/GDPR_DPA_Requirements_norms.json"
OUTPUT_DIR = "../../output/embeddings"
FAILURE_DIR = "../../output/failures"
# ==============================================================================


# ==================== 預設參數 (Default Parameters) ====================
API_TIMEOUT = 60
RETRY_WAIT_EXPONENTIAL_MULTIPLIER = 1
RETRY_WAIT_EXPONENTIAL_MAX = 10
EMBEDDING_BATCH_SIZE = 100
# ===========================================================================


class EmbeddingGenerator:
    """core_sentence Embedding 生成器"""

    def __init__(self, api_key: str):
        self.client = OpenAI(api_key=api_key, timeout=API_TIMEOUT)
        self.failures = []

    @retry(
        stop=stop_after_attempt(MAX_RETRIES),
        wait=wait_exponential(
            multiplier=RETRY_WAIT_EXPONENTIAL_MULTIPLIER,
            max=RETRY_WAIT_EXPONENTIAL_MAX
        )
    )
    def generate_batch_embeddings(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批次生成 embeddings（過濾空字串,空的回 None）"""
        embeddings: List[Optional[List[float]]] = []

        for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[i:i + EMBEDDING_BATCH_SIZE]

            filtered_batch = []
            empty_indices = set()
            for idx, text in enumerate(batch):
                if text and text.strip():
                    filtered_batch.append(text)
                else:
                    empty_indices.add(idx)
                    print(f" 跳過空 core_sentence (位置 {i + idx})")

            if not filtered_batch:
                embeddings.extend([None] * len(batch))
                continue

            response = self.client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=filtered_batch
            )
            cost_meter.add_embedding(response)
            batch_embeddings = [data.embedding for data in response.data]

            filtered_idx = 0
            for idx in range(len(batch)):
                if idx in empty_indices:
                    embeddings.append(None)
                else:
                    embeddings.append(batch_embeddings[filtered_idx])
                    filtered_idx += 1

            print(f"  已處理 {min(i + EMBEDDING_BATCH_SIZE, len(texts))}/{len(texts)} 個 embeddings")

        return embeddings

    def process_norms(self, norms: List[Dict]) -> List[Dict]:
        """對每條 norm 的 core_sentence 生成 embedding,寫回 norm["embedding"]。"""
        texts = [n.get("core_sentence") or "" for n in norms]
        embeds = self.generate_batch_embeddings(texts)

        for norm, embed in zip(norms, embeds):
            if embed:
                norm["embedding"] = embed
            else:
                norm["embedding"] = None
                self.failures.append({
                    "clause_id": norm.get("clause_id"),
                    "core_sentence": norm.get("core_sentence"),
                    "error": "Empty core_sentence or failed after retries",
                })
        return norms


def main():
    parser = argparse.ArgumentParser(description="Step 4: Generate core_sentence embeddings for norms")
    parser.add_argument("--input", type=str, help="Input norms JSON file path")
    parser.add_argument("--output-dir", type=str, help="Output directory path")
    parser.add_argument("--failure-dir", type=str, help="Failure log directory path")
    args = parser.parse_args()

    input_path = Path(args.input if args.input else INPUT_PATH)
    output_dir = Path(args.output_dir if args.output_dir else OUTPUT_DIR)
    failure_dir = Path(args.failure_dir if args.failure_dir else FAILURE_DIR)

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError("未找到 OPENAI_API_KEY 環境變數,請檢查 .env 檔案")
    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入文件: {input_path}")

    output_dir.mkdir(parents=True, exist_ok=True)
    failure_dir.mkdir(parents=True, exist_ok=True)

    print(f"讀取 norms: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        norms = json.load(f)
    print(f"共有 {len(norms)} 條 norms")

    document_name = input_path.stem.replace("_norms", "")
    cost_meter.configure(system="main", step=f"step4_embed_{document_name}")
    generator = EmbeddingGenerator(api_key)
    norms = generator.process_norms(norms)
    cost_meter.flush()

    n_ok = sum(1 for n in norms if n.get("embedding"))
    print(f"\nEmbedding 生成完成: {n_ok}/{len(norms)}")

    output_filename = f"{document_name}_embeddings.json"
    output_path = output_dir / output_filename

    output_data = {
        "document": document_name,
        "embedding_model": EMBEDDING_MODEL,
        "norms": norms,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2, ensure_ascii=False)

    print(f"Embeddings 已輸出至: {output_path}")

    if generator.failures:
        failure_path = failure_dir / "embedding_failures.json"
        if failure_path.exists():
            with open(failure_path, "r", encoding="utf-8") as f:
                existing = json.load(f)
            existing.extend(generator.failures)
            all_failures = existing
        else:
            all_failures = generator.failures
        with open(failure_path, "w", encoding="utf-8") as f:
            json.dump(all_failures, f, indent=2, ensure_ascii=False)
        print(f"失敗記錄已輸出至: {failure_path}")


if __name__ == "__main__":
    main()
