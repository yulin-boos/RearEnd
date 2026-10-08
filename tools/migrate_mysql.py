"""Import existing SQLite/JSON data into an initialized, empty MySQL business schema."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import time

from Common.config import PROJECT_ROOT, ROOT, Settings
from Common.database import Database, TABLES, json_text, utc_datetime
from Knowledge.mysql_store import search_text
from Knowledge.schemas import KnowledgeDraft
from Shop.catalog import CATEGORY_NAMES, ProductCatalog


def sqlite_snapshot(path, destination, names, apply):
    if not path.exists():
        return {name: [] for name in names}
    if apply:
        with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
            with sqlite3.connect(destination) as target:
                source.backup(target)
        return sqlite_snapshot(destination, destination, names, False)
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as source:
        source.row_factory = sqlite3.Row
        source.execute("BEGIN")
        present = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        rows = {name: [dict(row) for row in source.execute('SELECT * FROM "' + name + '"')] if name in present else []
                for name in names}
        source.rollback()
    return rows


def collect(settings, backup_dir, apply):
    catalog = ProductCatalog(settings.product_data_dir)
    catalog.ensure_loaded()  # Validate all models, paths and referenced image files before writes.
    products = json.loads((settings.product_data_dir / "products.json").read_text(encoding="utf-8"))
    if apply:
        backup_dir.mkdir(parents=True, exist_ok=False)
        if (ROOT / ".env").exists():
            shutil.copyfile(ROOT / ".env", backup_dir / "visual.env.backup")
    chat = sqlite_snapshot(settings.chat_db_path, backup_dir / "chat.sqlite3", ["users", "auth_sessions", "conversations"], apply)
    knowledge = sqlite_snapshot(settings.knowledge_db_path, backup_dir / "knowledge.sqlite3", ["knowledge_entries"], apply)
    image_hashes = {}
    for product in products:
        for image in product["image_metadata"]:
            file_hash = hashlib.sha256((settings.product_data_dir / image["path"]).read_bytes()).hexdigest()
            if file_hash != image["sha256"]:
                raise ValueError("商品图片摘要校验失败：" + image["path"])
            image_hashes[image["path"]] = file_hash
    now = time.time()
    return {
        "users": chat["users"],
        "auth_sessions": [row for row in chat["auth_sessions"] if row["expires_at"] > now],
        "conversations": [row for row in chat["conversations"] if row["expires_at"] > now],
        "expired_conversations": sum(row["expires_at"] <= now for row in chat["conversations"]),
        "knowledge": knowledge["knowledge_entries"], "products": products,
        "image_hash_count": len(image_hashes),
    }


def import_rows(cursor, data):
    for row in data["users"]:
        cursor.execute("""INSERT INTO users (id,username,password_hash,nickname,phone,bio,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (row["id"], row["username"], row["password_hash"],
            row["nickname"], row["phone"], row["bio"], utc_datetime(row["created_at"]), utc_datetime(row["updated_at"])))
    for row in data["auth_sessions"]:
        cursor.execute("INSERT INTO auth_sessions (token_hash,user_id,created_at,expires_at) VALUES (%s,%s,%s,%s)",
                       (row["token_hash"], row["user_id"], utc_datetime(row["created_at"]), utc_datetime(row["expires_at"])))
    now = utc_datetime(time.time())
    for row in data["conversations"]:
        context = json.loads(row["context"])
        general = context.get("conversation_type") == "general"
        if not general:
            context["legacy_recognition"] = True
        cursor.execute("""INSERT INTO conversations
            (id,user_id,kind,title,context_snapshot,revision,expires_at,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (row["recognition_id"], row.get("user_id"),
            "general" if general else "recognition", "智能病虫害问答" if general else "叶片识别咨询",
            json_text(context), row["revision"], utc_datetime(row["expires_at"]), now, now))
        history = json.loads(row["history"])
        if len(history) % 2 or any(item["role"] != ("user" if index % 2 == 0 else "assistant") for index, item in enumerate(history)):
            raise ValueError("旧会话历史不是完整问答轮次，迁移已回滚。")
        first_turn = row["revision"] - len(history) // 2 + 1
        for index, item in enumerate(history):
            cursor.execute("INSERT INTO chat_messages (conversation_id,turn_no,role,content,created_at) VALUES (%s,%s,%s,%s,%s)",
                           (row["recognition_id"], first_turn + index // 2, item["role"], item["content"], now))
    for row in data["knowledge"]:
        draft = KnowledgeDraft.model_validate_json(row["content"])
        cursor.execute("""INSERT INTO knowledge_entries
            (id,fingerprint,title,crop,condition_name,question,answer,applicability,uncertainty,keywords,
             search_text,source_context_id,deepseek_model,jev_model,created_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (row["id"], row["fingerprint"],
            draft.title, draft.crop, draft.condition, draft.question, draft.answer, draft.applicability,
            draft.uncertainty, json_text(draft.keywords), search_text(draft), row["source_recognition_id"],
            row["deepseek_model"], row["jev_model"], utc_datetime(row["created_at"])))
    for position, (code, name) in enumerate(CATEGORY_NAMES.items()):
        cursor.execute("INSERT INTO product_categories (code,name,sort_order) VALUES (%s,%s,%s)", (code, name, position))
    product_fields = ["id", "source_id", "name", "title", "spec", "price", "price_max", "currency", "unit", "min_order",
                      "min_order_text", "price_note", "description", "description_kind", "source_description", "attributes",
                      "keywords", "variant_scope", "image_scope", "source_image_urls", "source_url", "source_platform", "seller", "shipping_from"]
    sql_fields = product_fields + ["category_code", "main_image_position", "fetched_at", "fetched_at_original", "sort_order"]
    product_rows, variant_rows, image_rows = [], [], []
    for position, product in enumerate(data["products"]):
        values = []
        for field in product_fields:
            value = product[field]
            if field in ("attributes", "keywords", "source_image_urls"):
                value = json_text(value)
            elif field in ("price", "price_max", "min_order"):
                value = Decimal(str(value))
            values.append(value)
        fetched_at = datetime.fromisoformat(product["fetched_at"]).astimezone(timezone.utc).replace(tzinfo=None)
        values += [product["category"], product["images"].index(product["image"]), fetched_at, product["fetched_at"], position]
        product_rows.append(values)
        for variant_position, variant in enumerate(product["variants"]):
            variant_rows.append((product["id"], variant_position, variant["name"],
                Decimal(str(variant["price"])), Decimal(str(variant["price_max"])), variant["unit"], variant["price_text"],
                Decimal(str(variant["min_order"])) if variant["min_order"] is not None else None, variant["min_order_text"]))
        metadata = {item["path"]: item for item in product["image_metadata"]}
        for image_position, path in enumerate(product["images"]):
            image = metadata[path]
            image_rows.append((product["id"], image_position, path, image["source_url"], image["width"], image["height"], image["sha256"]))
    cursor.executemany("INSERT INTO products (" + ",".join(sql_fields) + ") VALUES (" + ",".join(["%s"] * len(sql_fields)) + ")", product_rows)
    cursor.executemany("""INSERT INTO product_variants
        (product_id,position,name,price,price_max,unit,price_text,min_order,min_order_text)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""", variant_rows)
    cursor.executemany("""INSERT INTO product_images
        (product_id,position,storage_path,source_url,width,height,sha256) VALUES (%s,%s,%s,%s,%s,%s,%s)""", image_rows)


def migrate(settings, apply=False):
    if settings.database_backend != "mysql":
        raise ValueError("请先配置 DATABASE_BACKEND=mysql 及目标业务库连接。")
    backup_dir = PROJECT_ROOT / ".reorganization-backup" / ("before-mysql-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    data = collect(settings, backup_dir, apply)
    report = {"database": settings.db_name, "applied": apply, "source_users": len(data["users"]),
              "active_sessions": len(data["auth_sessions"]), "active_conversations": len(data["conversations"]),
              "expired_conversations_in_backup": data["expired_conversations"], "knowledge_entries": len(data["knowledge"]),
              "products": len(data["products"]), "variants": sum(len(row["variants"]) for row in data["products"]),
              "images": data["image_hash_count"]}
    if not apply:
        return report
    database = Database(settings)
    try:
        database.verify_schema()
        with database.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", (settings.db_name + "_migration",))
            if cursor.fetchone()["acquired"] != 1:
                raise RuntimeError("另一个迁移正在执行，请稍后重试。")
            try:
                for table in sorted(TABLES):
                    cursor.execute(f"SELECT COUNT(*) AS total FROM `{table}`")
                    if cursor.fetchone()["total"]:
                        raise ValueError("目标业务库不是空库，拒绝覆盖已有数据。")
                import_rows(cursor, data)
                cursor.connection.commit()
            finally:
                cursor.execute("SELECT RELEASE_LOCK(%s)", (settings.db_name + "_migration",))
        report["backup_dir"] = str(backup_dir)
        (backup_dir / "migration-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report
    finally:
        database.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Back up local sources and import into an empty target schema")
    args = parser.parse_args()
    print(json.dumps(migrate(Settings.from_env(), args.apply), ensure_ascii=True, indent=2))
