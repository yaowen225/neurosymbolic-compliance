"""
Step 5: Knowledge Graph Writer

把 norms(含 core_sentence embedding)寫入 Neo4j。

節點:
  - Document {document_id, name}
  - Norm {clause_id, source_text, modality, logic_type, belongs_to,
          obligation_groups, core_sentence, embedding, document}
  - 核心欄位節點:Actor / Action / Object / Recipient —— 每條 norm 各自獨立節點,
    不共享(node id = "{clause_id}_actor" 等),避免高連接度中心節點。
  - Qualifier 節點(做法 C):Norm -[:HAS_QUALIFIER]-> Qualifier,Qualifier 再連
    condition / timing / manner / target / location / cause 值節點(有值才連)。

關係:
  - Document -[:CONTAINS]-> Norm
  - Norm -[:HAS_ACTOR/HAS_ACTION/HAS_OBJECT/HAS_RECIPIENT]-> 值節點
  - Norm -[:HAS_QUALIFIER]-> Qualifier -[:HAS_CONDITION/...]-> 值節點
  - Norm -[:HAS_CHILD]-> Norm
      法規端:依 belongs_to(功能性,供 Step 7 聚合)
      合約端:依 parser 的 parent(忠實表示原文層級,無比對作用)
      parent 指向不存在的節點 → 當 standalone,跳過不報錯。

注意:本腳本一次寫入一份文件(法規或合約)。法規與合約 clause_id 不衝突,
      兩份都寫進同一個 DB,Step 6 透過 Document 區分。
      CLEAR_DATABASE 預設 False。

使用方式:
    python kg_writer.py --input ../../output/embeddings/GDPR_DPA_Requirements_embeddings.json
    python kg_writer.py --input ../../output/embeddings/Online124_embeddings.json
    python kg_writer.py --input ... --clear      # 清空 DB 後再寫(小心)
"""

import json
import argparse
import yaml
import sys
from pathlib import Path
from typing import List, Dict
from neo4j import GraphDatabase
from neo4j.exceptions import ServiceUnavailable, AuthError

if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')


# ==================== 實驗參數 (Experimental Parameters) ====================
INPUT_PATH = "../../output/embeddings/GDPR_DPA_Requirements_embeddings.json"
CONFIG_PATH = "../../config.yaml"

# 是否清空資料庫後再寫入 (小心使用)
CLEAR_DATABASE = False
# ==============================================================================


# ==================== 預設參數 (Default Parameters) ====================
CONNECTION_TIMEOUT = 30
QUALIFIER_FIELDS = ["condition", "timing", "manner", "target", "location", "cause"]
# 各 qualifier 對應的關係名稱
QUALIFIER_REL = {
    "condition": "HAS_CONDITION",
    "timing": "HAS_TIMING",
    "manner": "HAS_MANNER",
    "target": "HAS_TARGET",
    "location": "HAS_LOCATION",
    "cause": "HAS_CAUSE",
}
# ===========================================================================


class KGWriter:
    """Norm-based 知識圖譜寫入器"""

    def __init__(self, uri: str, username: str, password: str, database: str = "neo4j"):
        try:
            self.driver = GraphDatabase.driver(
                uri, auth=(username, password),
                max_connection_lifetime=CONNECTION_TIMEOUT
            )
            self.database = database
            self.driver.verify_connectivity()
            print(f"Neo4j 連線成功: {uri} (database={database})")
        except AuthError:
            raise ValueError("Neo4j 認證失敗,請檢查 config.yaml 中的帳號密碼")
        except ServiceUnavailable:
            raise ConnectionError("無法連線到 Neo4j,請確認 Neo4j 正在運行")
        except Exception as e:
            raise Exception(f"Neo4j 連線錯誤: {str(e)}")

    def close(self):
        if self.driver:
            self.driver.close()
            print("Neo4j 連線已關閉")

    def clear_database(self):
        with self.driver.session(database=self.database) as session:
            session.run("MATCH (n) DETACH DELETE n")
            print(" 資料庫已清空")

    def create_indexes(self):
        with self.driver.session(database=self.database) as session:
            for idx in [
                "CREATE INDEX IF NOT EXISTS FOR (n:Norm) ON (n.clause_id)",
                "CREATE INDEX IF NOT EXISTS FOR (n:Document) ON (n.document_id)",
            ]:
                session.run(idx)
            print("索引建立完成")

    def write_norm(self, tx, norm: Dict, document_name: str):
        """寫入單一 norm 的所有節點與關係(不含 HAS_CHILD,父子關係另一 pass 處理)。"""
        clause_id = norm["clause_id"]

        # 1. Norm 節點
        tx.run("""
            MERGE (n:Norm {clause_id: $clause_id})
            SET n.source_text       = $source_text,
                n.modality          = $modality,
                n.logic_type        = $logic_type,
                n.belongs_to        = $belongs_to,
                n.parent            = $parent,
                n.origin_clause     = $origin_clause,
                n.obligation_groups = $obligation_groups,
                n.core_sentence     = $core_sentence,
                n.embedding         = $embedding,
                n.document          = $document,
                n.name              = $clause_id
        """,
            clause_id=clause_id,
            source_text=norm.get("source_text"),
            modality=norm.get("modality"),
            logic_type=norm.get("logic_type"),
            belongs_to=norm.get("belongs_to"),
            parent=norm.get("parent"),
            origin_clause=norm.get("origin_clause") or clause_id,
            obligation_groups=norm.get("obligation_groups", []) or [],
            core_sentence=norm.get("core_sentence"),
            embedding=norm.get("embedding"),
            document=document_name,
        )

        # 2. Document -> Norm
        tx.run("""
            MERGE (d:Document {document_id: $document})
            SET d.name = $document
            WITH d
            MATCH (n:Norm {clause_id: $clause_id})
            MERGE (d)-[:CONTAINS]->(n)
        """, document=document_name, clause_id=clause_id)

        # 3. 核心欄位節點(各自獨立,不共享)
        core_specs = [
            ("actor", "Actor", "HAS_ACTOR"),
            ("action", "Action", "HAS_ACTION"),
            ("object", "Object", "HAS_OBJECT"),
            ("recipient", "Recipient", "HAS_RECIPIENT"),
        ]
        for field, label, rel in core_specs:
            value = norm.get(field)
            if value is None or (isinstance(value, str) and not value.strip()):
                continue
            node_id = f"{clause_id}_{field}"
            tx.run(f"""
                MERGE (v:{label} {{node_id: $node_id}})
                SET v.value = $value, v.name = $value, v.owner = $clause_id
                WITH v
                MATCH (n:Norm {{clause_id: $clause_id}})
                MERGE (n)-[:{rel}]->(v)
            """, node_id=node_id, value=value, clause_id=clause_id)

        # 4. Qualifier 節點 + 值節點(有值才建)
        present_quals = [
            (f, norm.get(f)) for f in QUALIFIER_FIELDS
            if norm.get(f) is not None and str(norm.get(f)).strip()
        ]
        if present_quals:
            qual_id = f"{clause_id}_qual"
            tx.run("""
                MERGE (q:Qualifier {node_id: $qual_id})
                SET q.owner = $clause_id, q.name = $qual_id
                WITH q
                MATCH (n:Norm {clause_id: $clause_id})
                MERGE (n)-[:HAS_QUALIFIER]->(q)
            """, qual_id=qual_id, clause_id=clause_id)

            for field, value in present_quals:
                rel = QUALIFIER_REL[field]
                node_id = f"{clause_id}_{field}"
                label = field.capitalize()
                tx.run(f"""
                    MERGE (v:{label} {{node_id: $node_id}})
                    SET v.value = $value, v.name = $value, v.owner = $clause_id
                    WITH v
                    MATCH (q:Qualifier {{node_id: $qual_id}})
                    MERGE (q)-[:{rel}]->(v)
                """, node_id=node_id, value=value, qual_id=qual_id, clause_id=clause_id)

    def write_all(self, norms: List[Dict], document_name: str):
        total = len(norms)
        print(f"\n開始寫入 {total} 條 norms (document={document_name})...")
        with self.driver.session(database=self.database) as session:
            with session.begin_transaction() as tx:
                for i, norm in enumerate(norms, 1):
                    try:
                        self.write_norm(tx, norm, document_name)
                        if i % 20 == 0 or i == total:
                            print(f"  已寫入 {i}/{total} 條")
                    except Exception as e:
                        print(f" 寫入失敗: {norm.get('clause_id')} - {str(e)}")
                        raise
                tx.commit()
        print(f"{total} 條 norms 已寫入 (Transaction 已提交)")

    def create_child_relationships(self, norms: List[Dict]):
        """
        建立 HAS_CHILD:
          - 法規端:child.belongs_to → parent.clause_id
          - 合約端:child.parent → parent.clause_id
        parent 不存在 → 跳過(當 standalone),不報錯。
        """
        existing_ids = {n["clause_id"] for n in norms}
        created, skipped = 0, 0
        with self.driver.session(database=self.database) as session:
            for norm in norms:
                child_id = norm["clause_id"]
                parent_id = norm.get("belongs_to") or norm.get("parent")
                if not parent_id:
                    continue
                if parent_id not in existing_ids:
                    print(f" {child_id} 的 parent '{parent_id}' 不存在,當 standalone 處理")
                    skipped += 1
                    continue
                session.run("""
                    MATCH (p:Norm {clause_id: $parent_id})
                    MATCH (c:Norm {clause_id: $child_id})
                    MERGE (p)-[:HAS_CHILD]->(c)
                """, parent_id=parent_id, child_id=child_id)
                created += 1
        print(f"HAS_CHILD 關係已建立: {created} 條" + (f"(略過 {skipped} 條)" if skipped else ""))


def load_config(config_path: str) -> Dict:
    with open(config_path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def main():
    parser = argparse.ArgumentParser(description="Step 5: Write norm-based KG to Neo4j")
    parser.add_argument("--input", type=str, help="Embeddings JSON (*_embeddings.json)")
    parser.add_argument("--config", type=str, help="Config YAML file path")
    parser.add_argument("--clear", action="store_true", help="Clear database before writing")
    args = parser.parse_args()

    input_path = Path(args.input if args.input else INPUT_PATH)
    config_path = Path(args.config if args.config else CONFIG_PATH)
    clear_db = args.clear or CLEAR_DATABASE

    if not input_path.exists():
        raise FileNotFoundError(f"找不到輸入文件: {input_path}")
    if not config_path.exists():
        raise FileNotFoundError(f"找不到配置文件: {config_path}")

    config = load_config(str(config_path))
    neo4j_config = config["neo4j"]

    print(f"讀取 embeddings: {input_path}")
    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    norms = data["norms"]
    document_name = data.get("document") or input_path.stem.replace("_embeddings", "")
    print(f"共有 {len(norms)} 條 norms (document={document_name})")

    writer = KGWriter(
        uri=neo4j_config["uri"],
        username=neo4j_config["username"],
        password=neo4j_config["password"],
        database=neo4j_config.get("database", "neo4j"),
    )

    try:
        if clear_db:
            print(" CLEAR_DATABASE 啟用,清空整個資料庫...")
            writer.clear_database()

        writer.create_indexes()
        writer.write_all(norms, document_name)
        writer.create_child_relationships(norms)

        print("\n寫入完成。可在 Neo4j Browser 檢視:")
        print("   MATCH (n:Norm) RETURN n LIMIT 25")
        print("   MATCH (p:Norm)-[:HAS_CHILD]->(c:Norm) RETURN p,c LIMIT 25")
    finally:
        writer.close()


if __name__ == "__main__":
    main()
