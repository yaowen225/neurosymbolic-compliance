"""
core_sentence_postproc.py — 共用後處理(抽取後、embedding 前):重建 parser 列舉 child 的 core_sentence。

性質與 apply_group_inheritance 相同:純 code、不呼叫 LLM、抽取結束就地處理 norm 清單。
法規端與合約端都會用到(reg 的 R7_a~d 這種 actor==null 列舉 child 也需要重建),
因此抽成共用 helper,由 extract_regulatory / extract_contract 在存檔前各自呼叫。

問題:parser 切出的列舉 child(如 sub_a_2.1_i "Customers")actor=null,抽取時 core_sentence
被生成成通用框架句(如 "The required content includes Customers."),丟掉 parent 的關鍵語意,
導致跟對應 norm 的 cosine 太低、卡在 6b。

修法(只改 core_sentence,其他欄位不動):
  觸發:有可解析 parent(parent 或 belongs_to 指到存在的 norm)且 actor==null 且 object 非空。
       用 actor==null(不是 action=='include',後者會誤觸 child 如 R8_a 把本來好的 core 改壞)。
  重建:把 parent.object 開頭的通用框架詞("Personal Data about the following"/"the following"/"the")
       去掉得 cleaned;core_sentence = f"The {cleaned} include {child.object}."。保留 child.object。
  例:R6 sub_a_2.1_i -> "The categories of data subjects include Customers."
      R5 sub_a_1.1_i -> "The types of Personal Data include Ordinary contact information ..."
"""

# 去掉的通用框架前綴(小寫比對,長的先比)
FRAMEWORK_PREFIXES = ["personal data about the following", "the following", "the"]


def clean_parent_object(obj: str) -> str:
    s = (obj or "").strip()
    low = s.lower()
    for pre in FRAMEWORK_PREFIXES:
        if low.startswith(pre):
            s = s[len(pre):].strip()
            break
    return s


def rebuild_list_item_core_sentence(norms):
    """就地重建列舉 child 的 core_sentence,回傳變動清單 [(clause_id, old, new), ...]。"""
    by_id = {n["clause_id"]: n for n in norms}
    changed = []
    for n in norms:
        if n.get("actor") is not None:            # 只處理 actor==null 的列舉 child
            continue
        obj = n.get("object")
        if not obj or not str(obj).strip():        # object 非空
            continue
        pid = n.get("parent") or n.get("belongs_to")
        if not pid or pid not in by_id:            # 可解析 parent
            continue
        parent = by_id[pid]
        pobj = parent.get("object")
        if not pobj or not str(pobj).strip():
            continue
        cleaned = clean_parent_object(pobj)
        if not cleaned:
            continue
        new_core = f"The {cleaned} include {str(obj).strip()}."
        if new_core != n.get("core_sentence"):
            changed.append((n["clause_id"], n.get("core_sentence"), new_core))
            n["core_sentence"] = new_core
    return changed
