"""
Regulatory Parser for GDPR Articles
將法規文件 (如 GDPR_DPA_Requirements.txt) 解析成 JSON 格式

沿用 src_old 切分邏輯:只把文件切成 R1–R46 各一條,不拆內部列舉
(R8 的 (a)(b)(c)(d) 留在同一條文字裡,由 Step 3 抽取時展開成子 norm)。

使用方式:
    # 開發/測試: 直接修改下方路徑後執行
    python regulatory_parser.py

    # 生產/批次: 使用命令列參數
    python regulatory_parser.py --input path/to/input.txt --output-dir path/to/output/
"""

import re
import json
import argparse
from pathlib import Path
from typing import List, Dict


# ==================== 實驗參數 (Experimental Parameters) ====================
# 開發測試時直接修改這裡的路徑

INPUT_PATH = "../../data/input/regulatory/GDPR_DPA_Requirements.txt"
OUTPUT_DIR = "../../output/parsed_regulatory"

# ==============================================================================


class RegulatoryParser:
    """法規文件解析器"""

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)
        self.document_id = self.input_path.stem

    def parse(self) -> List[Dict]:
        """
        解析法規文件,返回標準化的 clause 列表

        Returns:
            List[Dict]: 每個 dict 只包含 clause_id 和 text
        """
        with open(self.input_path, 'r', encoding='utf-8') as f:
            content = f.read()

        clauses = []

        # 正則表達式: 匹配如 "R1", "R2" 開頭的條款
        pattern = r'(R\d+)\s+(.*?)(?=R\d+|\Z)'
        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            clause_id = match.group(1).strip()
            text = match.group(2).strip()

            # 清理文本格式
            text = self._clean_text(text)

            if not text:
                continue

            clause_data = {
                "clause_id": clause_id,
                "text": text
            }

            clauses.append(clause_data)

        return clauses

    def _clean_text(self, text: str) -> str:
        """
        清理文本格式:
        - 合併多行為單一字串
        - 移除多餘空白
        - 保留所有實質內容
        """
        # 移除換行符,保留空格
        text = text.replace('\n', ' ')

        # 將多個空格縮減為單一空格
        text = re.sub(r'\s+', ' ', text)

        # 前後去空白
        text = text.strip()

        return text


def main():
    """主程式:解析法規文件並輸出 JSON"""

    # 解析命令列參數
    parser = argparse.ArgumentParser(description='Parse regulatory documents')
    parser.add_argument('--input', type=str, help='Input file path')
    parser.add_argument('--output-dir', type=str, help='Output directory path')
    args = parser.parse_args()

    # 使用命令列參數或預設值
    input_path = Path(args.input if args.input else INPUT_PATH)
    output_dir = Path(args.output_dir if args.output_dir else OUTPUT_DIR)

    # 檢查輸入文件
    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入文件: {input_path}")

    # 建立輸出目錄
    output_dir.mkdir(parents=True, exist_ok=True)

    # 解析文件
    print(f"正在解析法規文件: {input_path}")
    reg_parser = RegulatoryParser(str(input_path))
    clauses = reg_parser.parse()

    print(f"成功解析 {len(clauses)} 條法規條款")

    # 輸出 JSON (檔名跟隨輸入檔名)
    output_filename = f"{input_path.stem}_parsed.json"
    output_path = output_dir / output_filename

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(clauses, f, indent=2, ensure_ascii=False)

    print(f"已輸出至: {output_path}")

    # 顯示前兩條作為預覽
    print("\n=== 預覽前兩條 ===")
    for i, clause in enumerate(clauses[:2], 1):
        print(f"\n條款 {i}:")
        print(f"  ID: {clause['clause_id']}")
        print(f"  文本: {clause['text'][:100]}...")


if __name__ == "__main__":
    main()
