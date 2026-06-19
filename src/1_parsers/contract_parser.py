"""
Contract Parser for DPA Contracts
將合約文件 (如 Online124.txt) 解析成 JSON 格式
支援階層式條款結構 (如 5.7.1, 5.7.2 及其子項 (i)(ii)(iii))

沿用 src_old 邏輯:切條款 + 拆 (i)(ii) 子項,輸出 parent 欄位,保留逐字原文。
合約端不做列舉展開,Step 3 抽取以 parser 切出的每一條(含子項)為單位。

使用方式:
    # 開發/測試: 直接修改下方路徑後執行
    python contract_parser.py

    # 生產/批次: 使用命令列參數
    python contract_parser.py --input path/to/input.txt --output-dir path/to/output/
"""

import re
import json
import argparse
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from collections import Counter


# ==================== 實驗參數 (Experimental Parameters) ====================
# 開發測試時直接修改這裡的路徑

INPUT_PATH = "../../data/input/contracts/Online124.txt"
OUTPUT_DIR = "../../output/parsed_contracts"

# ==============================================================================


class ContractParser:
    """合約文件解析器"""

    def __init__(self, input_path: str):
        self.input_path = Path(input_path)
        self.document_id = self.input_path.stem

    def parse(self) -> List[Dict]:
        """
        解析合約文件,返回標準化的 clause 列表

        Returns:
            List[Dict]: 每個 dict 包含 clause_id, text, parent
        """
        with open(self.input_path, 'r', encoding='utf-8') as f:
            content = f.read()

        clauses = []

        # Step 1: 按章節分割文件 (Appendix A / Sub-appendix A / Sub-appendix B)
        sections = self._split_into_sections(content)

        # Step 2: 先收齊所有主條款,再做 clause_id 唯一化(只有真的撞號才改;沒撞號完全不變),
        #         最後才拆 (i)(ii) 子項。動機:有些合約(如 Online39)主文與附錄的編號會重置撞號
        #         (主文 3.1 與附錄 3.1),唯一化後子項才不會跟著一起撞。
        main_clauses = []
        for prefix, section_text in sections:
            main_clauses.extend(self._split_main_clauses(section_text, prefix))
        main_clauses = self._dedupe_clause_ids(main_clauses)

        # 偵測「標題(以冒號結尾)+ 其數字巢狀清單項」結構,得 {清單項 clause_id: 標題 clause_id}。
        # 只有真的存在這種結構時才會掛 parent;沒有的合約(如 Online124)得到空 map、完全不受影響。
        list_parent = self._link_list_items(main_clauses)
        # 再偵測「列舉前導句(introduces categories/types of data,但本身無內嵌 (i)(ii))+ 緊接其後
        # 連續同層『手足』編號條款」結構(如 Online39 的 3.1 標題 + 3.2~3.5 列舉)。冒號標題優先。
        for child_id, head_id in self._link_sibling_enumerations(main_clauses).items():
            list_parent.setdefault(child_id, head_id)

        # Step 3: 處理每個條款(拆 (i)(ii) 子項)
        for clause_id, clause_text in main_clauses:
            if self._has_enumeration(clause_text):
                main_text, enum_items = self._split_enumeration(clause_text)

                if main_text.strip():
                    clauses.append({
                        "clause_id": f"{clause_id}_main",
                        "text": self._clean_text(main_text),
                        "parent": list_parent.get(clause_id)
                    })

                for enum_index, enum_text in enum_items:
                    clauses.append({
                        "clause_id": f"{clause_id}_{enum_index}",
                        "text": self._clean_text(enum_text),
                        "parent": f"{clause_id}_main" if main_text.strip() else None
                    })
            else:
                clauses.append({
                    "clause_id": clause_id,
                    "text": self._clean_text(clause_text),
                    "parent": list_parent.get(clause_id)
                })

        # Step 4: 最後保險,確保 final clause_id 全唯一(只有仍撞號才改;124 不變)
        clauses = self._ensure_unique_ids(clauses)
        return clauses

    def _ensure_unique_ids(self, clauses: List[Dict]) -> List[Dict]:
        """最後保險:把 final clause_id 仍重複者唯一化。殘留重複主要來自「單一條款內重複的
        (i)(ii) 列舉索引」(例如同一條 11.3 裡有兩組 (i)(ii)(iii))—— 這些都是 leaf 子項、
        不會被任何 clause 當 parent,故直接加尾碼即可,不需動 parent。沒重複則完全不動。"""
        counts = Counter(c["clause_id"] for c in clauses)
        if all(n == 1 for n in counts.values()):
            return clauses
        seen: Dict[str, int] = {}
        for c in clauses:
            cid = c["clause_id"]
            if counts[cid] > 1:
                seen[cid] = seen.get(cid, 0) + 1
                c["clause_id"] = f"{cid}__{seen[cid]}"
        return clauses

    def _link_list_items(self, main_clauses: List[Tuple[str, str]]) -> Dict[str, str]:
        """偵測「以冒號結尾的標題(如 '... categories of Data Subjects ...:')+ 其數字巢狀清單項
        (X.Y -> X.Y.1, X.Y.2 ...)」結構,回傳 {清單項 clause_id: 標題 clause_id}。

        結構性、無合約特例:
        - 在文件順序中,某條款文字以 ':' 結尾 => 視為「清單標題」,記住它(以其數字 base 為鍵)。
        - 之後出現、數字上是其子層(child base 去掉最後一段 == 標題 base)的條款 => 掛到該標題。
        因此沒有「冒號標題 + 數字清單」結構的合約(如 Online124,其數字子句的標題不以冒號結尾)
        會得到空 map -> parent 全 None -> 輸出不變。在 main_clauses 階段判斷(列舉父條款此時文字
        尚未以冒號結尾,不會被誤判)。"""
        def base(cid: str) -> str:
            return cid.split("__")[0]   # 去掉 de-collision 尾碼,取原始數字編號

        recent_colon: Dict[str, str] = {}   # numeric base -> heading clause_id(最近一個)
        parent: Dict[str, str] = {}
        for cid, text in main_clauses:
            b = base(cid)
            if "." in b:
                pbase = b.rsplit(".", 1)[0]
                if pbase in recent_colon:
                    parent[cid] = recent_colon[pbase]
            if text.rstrip().endswith(":"):
                recent_colon[b] = cid
        return parent

    # 列舉前導句的觸發詞:本身宣告「(the) following ... categor/type/kind ...」一串清單。
    _SIBLING_LEADIN_RE = re.compile(r'following\b[^.:]*\b(?:categor|type|kind)\w*\b', re.IGNORECASE)

    def _link_sibling_enumerations(self, main_clauses: List[Tuple[str, str]]) -> Dict[str, str]:
        """偵測「列舉前導句 + 緊接其後連續同層手足編號條款」結構,回傳 {手足 clause_id: 前導句 clause_id}。

        結構性、無合約特例(不寫死任何編號):
        - 前導句條件:文字本身**沒有**內嵌 (i)(ii)(a)(b) 列舉(否則其成員是子項而非手足,
          交由既有 _split_enumeration / 冒號規則處理,如 Online124),且文字以「following … categor/type/kind …」
          宣告一串清單(_SIBLING_LEADIN_RE)。
        - 成員條件:在文件順序中,前導句之後**緊接**的連續「手足」條款 —— 同一父層(去掉末段相同)、
          末段數字由前導句末段 +1 起逐一遞增 —— 視為清單成員,掛到前導句。需 >=2 個成員才成立(避免誤掛)。
        因此沒有此結構的合約(如 Online124:其「following types of …」前導句自帶 (i)(ii) 子項、且後面不接
          手足編號)得到空 map -> parent 全 None -> 輸出完全不變。"""
        def base(cid: str) -> str:
            return cid.split("__")[0]
        parent: Dict[str, str] = {}
        n = len(main_clauses)
        for i, (cid, text) in enumerate(main_clauses):
            b = base(cid)
            if "." not in b:
                continue
            if self._has_enumeration(text):          # 自帶 (i)(ii) -> 成員是子項,非手足
                continue
            if not self._SIBLING_LEADIN_RE.search((text or "").strip()):
                continue
            prefix, seg = b.rsplit(".", 1)
            if not seg.isdigit():
                continue
            members: List[str] = []
            expect = int(seg) + 1
            j = i + 1
            while j < n:
                cj, _tj = main_clauses[j]
                bj = base(cj)
                if "." in bj:
                    pj, sj = bj.rsplit(".", 1)
                    if pj == prefix and sj.isdigit() and int(sj) == expect:
                        members.append(cj)
                        expect += 1
                        j += 1
                        continue
                break
            if len(members) >= 2:
                for m in members:
                    parent[m] = cid
        return parent

    def _dedupe_clause_ids(self, pairs: List[Tuple[str, str]]) -> List[Tuple[str, str]]:
        """讓 clause_id 唯一:**只有真的撞號**(同一 id 對到多個不同條款)時,才給該組每個成員加
        尾碼(__1, __2, ...)。沒撞號的 id 完全不動 —— 因此本來就無撞號的合約(如 Online124)
        輸出 byte-identical、不變。同一套 code、無合約特例分支。"""
        counts = Counter(cid for cid, _ in pairs)
        if all(n == 1 for n in counts.values()):
            return pairs   # 沒有任何撞號 -> 原樣返回(Online124 走這條,輸出不變)
        seen: Dict[str, int] = {}
        out: List[Tuple[str, str]] = []
        for cid, text in pairs:
            if counts[cid] > 1:
                seen[cid] = seen.get(cid, 0) + 1
                out.append((f"{cid}__{seen[cid]}", text))   # 撞號組:每個成員都加尾碼,保證唯一且可預期
            else:
                out.append((cid, text))
        return out

    def _split_into_sections(self, content: str) -> List[Tuple[str, str]]:
        """
        按章節標題分割文件，回傳 (prefix, section_text) 列表。

        已知章節:
          Appendix A        → prefix 'main_'
          Sub-appendix A    → prefix 'sub_a_'
          Sub-appendix B    → prefix 'sub_b_'
        """
        prefix_map = {
            'Appendix A': 'main_',
            'Sub-appendix A': 'sub_a_',
            'Sub-appendix B': 'sub_b_',
        }

        header_pattern = r'^(Appendix A|Sub-appendix A|Sub-appendix B)\s*$'
        matches = list(re.finditer(header_pattern, content, re.MULTILINE))

        if not matches:
            # 找不到任何章節標題，整份文件視為主文，不加前綴
            return [('', content)]

        sections = []
        for i, match in enumerate(matches):
            section_name = match.group(1)
            prefix = prefix_map.get(section_name, '')
            start = match.end()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
            sections.append((prefix, content[start:end]))

        return sections

    def _split_main_clauses(self, content: str, prefix: str = '') -> List[Tuple[str, str]]:
        """
        分割主要條款，加上章節前綴。

        修正: 使用 ^ 錨定至行首，避免誤匹配正文中的數字引用
        (如 "section 5.5, 5.6, 5.7 and 5.8 of this Agreement")
        """
        clauses = []
        # ^ 錨定行首 (re.MULTILINE)，只匹配真正位於行首的條款編號。
        # \.? = 編號後可有「可選的尾點」:吃 "1.1"(Online124)也吃 "1.1."(Online39);
        #        尾點不進 capture group,故 clause_id 兩種格式一致(都是 "1.1")。
        pattern = r'(?m)^(\d+\.\d+(?:\.\d+)?)\.?[ \t]+(.*?)(?=\n\d+\.\d+(?:\.\d+)?\.?[ \t]|\Z)'
        matches = re.finditer(pattern, content, re.DOTALL)

        for match in matches:
            clause_id = prefix + match.group(1).strip()
            clause_text = match.group(2).strip()

            if clause_text:
                clauses.append((clause_id, clause_text))

        return clauses

    def _has_enumeration(self, text: str) -> bool:
        """
        檢查是否包含枚舉項
        支援格式: (i), (ii), (a), (b), (1), (2)
        """
        patterns = [
            r'\([ivxlcdm]+\)',      # 羅馬數字: (i), (ii), (iii)
            r'\([a-z]\)',            # 小寫字母: (a), (b), (c)
            r'\([A-Z]\)',            # 大寫字母: (A), (B), (C)
            r'\(\d+\)'               # 阿拉伯數字: (1), (2), (3)
        ]

        for pattern in patterns:
            # 使用 \b 確保不會誤觸單字內部,並檢查前後是否為空白或標點
            if re.search(r'(?:^|\s)' + pattern + r'(?:\s|$)', text, re.IGNORECASE):
                return True
        return False

    def _split_enumeration(self, text: str) -> Tuple[str, List[Tuple[str, str]]]:
        """
        分割主句和枚舉項
        支援格式: (i), (ii), (a), (b), (1), (2)
        """
        # 嘗試所有枚舉格式,找出第一個匹配
        enum_patterns = [
            (r'\([ivxlcdm]+\)', r'\(([ivxlcdm]+)\)\s+(.*?)(?=\([ivxlcdm]+\)|\Z)'),  # 羅馬
            (r'\([a-z]\)', r'\(([a-z])\)\s+(.*?)(?=\([a-z]\)|\Z)'),                 # 小寫字母
            (r'\([A-Z]\)', r'\(([A-Z])\)\s+(.*?)(?=\([A-Z]\)|\Z)'),                 # 大寫字母
            (r'\(\d+\)', r'\((\d+)\)\s+(.*?)(?=\(\d+\)|\Z)')                        # 數字
        ]

        for first_pattern, full_pattern in enum_patterns:
            first_enum = re.search(first_pattern, text, re.IGNORECASE)

            if first_enum:
                # 找到匹配,分割主句和枚舉項
                main_text = text[:first_enum.start()].strip()

                enum_matches = re.finditer(full_pattern, text[first_enum.start():], re.DOTALL | re.IGNORECASE)

                enum_items = []
                for match in enum_matches:
                    enum_index = match.group(1).strip()
                    enum_text = match.group(2).strip()

                    if enum_text:
                        enum_items.append((enum_index, enum_text))

                return main_text, enum_items

        # 沒有找到任何枚舉項
        return text, []

    def _clean_text(self, text: str) -> str:
        """清理文本格式"""
        text = text.replace('\n', ' ')
        text = re.sub(r'\s+', ' ', text)
        text = text.strip()
        return text


def main():
    """主程式:解析合約文件並輸出 JSON"""

    # 解析命令列參數
    parser = argparse.ArgumentParser(description='Parse contract documents')
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
    print(f"正在解析合約文件: {input_path}")
    contract_parser = ContractParser(str(input_path))
    clauses = contract_parser.parse()

    print(f"成功解析 {len(clauses)} 條合約條款")

    # 統計
    main_clauses = [c for c in clauses if c['parent'] is None]
    enum_clauses = [c for c in clauses if c['parent'] is not None]
    print(f"  - 主條款: {len(main_clauses)} 條")
    print(f"  - 枚舉子項: {len(enum_clauses)} 條")

    # 輸出 JSON
    output_filename = f"{input_path.stem}_parsed.json"
    output_path = output_dir / output_filename

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(clauses, f, indent=2, ensure_ascii=False)

    print(f"已輸出至: {output_path}")

    # 顯示前三條作為預覽
    print("\n=== 預覽前三條 ===")
    for i, clause in enumerate(clauses[:3], 1):
        print(f"\n條款 {i}:")
        print(f"  ID: {clause['clause_id']}")
        if clause['parent']:
            print(f"  Parent: {clause['parent']}")
        print(f"  文本: {clause['text'][:100]}...")


if __name__ == "__main__":
    main()
