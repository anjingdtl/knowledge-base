"""知识库归纳整理服务 — 基于企业知识管理预设分类，LLM 负责归类"""
import difflib
import json
import logging
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.data.classification_schema import (
    CLASSIFICATION_SCHEMA,
    UNCATEGORIZED,
    get_all_codes,
    get_schema_prompt,
)
from src.services.db import Database
from src.services.llm import LLMService
from src.utils.config import Config

CLASSIFY_PROMPT = """你是一个企业知识库管理员。请将以下知识条目归入企业知识管理分类体系中。

预设分类体系：
{schema}

{dynamic_schema}要求：
1. 优先从预设分类中选择最具体的子类别
2. **必须为每个条目都指定分类，不允许遗漏任何条目**
3. 如果某条知识确实不属于任何预设分类，你可以创建新分类：
   - 设置 "is_new": true
   - 提供 "new_name"（简短名称，2-6字）
   - 提供 "new_description"（一句话描述该分类的覆盖范围）
   - 可选 "parent_code" 指定挂靠到某个预设大类下
4. **只有完全无法理解内容且不适合创建新分类时，才归入"Z 未分类"**
5. 每个条目只能出现一次
6. 根据标题关键词和摘要语义进行分类，公文/通知/管理办法按其业务主题归类

知识条目列表（共 {item_count} 条）：
{items}

请严格按以下 JSON 格式输出，不要输出其他内容：
[
  {{
    "code": "分类代码",
    "ids": [1, 2],
    "is_new": false
  }}
]

注意：
- ids 是条目的序号（从1开始），不要使用标题文字
- 输出条目总数必须等于输入条目总数（{item_count} 条）
- 不可遗漏任何条目"""

RECLASSIFY_PROMPT = """以下 {item_count} 条知识在上一轮分类中未被成功归类，请单独为它们指定分类。

预设分类体系：
{schema}

{dynamic_schema}知识条目：
{items}

请严格按以下 JSON 格式输出，不要遗漏任何条目：
[
  {{
    "code": "分类代码",
    "ids": [1],
    "is_new": false
  }}
]"""


def _smart_summary(content: str, max_len: int = 300) -> str:
    """从内容中提取有语义价值的摘要，跳过 PDF 格式头部"""
    if not content:
        return ""
    import re
    # 先清理行内格式标记
    cleaned = re.sub(r'\[第\d+页\]', '', content)
    cleaned = re.sub(r'—\s*\d+\s*—', '', cleaned)
    cleaned = re.sub(r'^[\s—－-＝=·]+$', '', cleaned, flags=re.MULTILINE)
    lines = cleaned.split("\n")
    meaningful = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) <= 3:
            continue
        if all(c in "—－-＝=· " for c in stripped):
            continue
        meaningful.append(stripped)
        if sum(len(text_line) for text_line in meaningful) >= max_len:
            break
    text = " ".join(meaningful)
    return text[:max_len]


def _normalize_subcategory(parent_code: str, child) -> dict:
    if isinstance(child, dict):
        code = str(child.get("code", "")).strip()
        name = str(child.get("name", code)).strip()
        description = str(child.get("description", "")).strip()
    elif isinstance(child, (tuple, list)) and len(child) >= 2:
        code = str(child[0]).strip()
        name = str(child[1]).strip()
        description = str(child[2]).strip() if len(child) >= 3 else ""
    else:
        code = str(child).strip()
        name = code
        description = ""
    return {
        "code": code,
        "name": name,
        "description": description,
        "parent_code": parent_code,
    }


def iter_classification_schema(schema=None):
    """Yield classification schema entries in a stable dict shape.

    Supports both the current list based schema and the legacy dict format.
    """
    source = CLASSIFICATION_SCHEMA if schema is None else schema
    if isinstance(source, dict):
        iterable = []
        for code, info in source.items():
            if isinstance(info, dict):
                iterable.append({
                    "code": str(info.get("code", code)).strip(),
                    "name": str(info.get("name", code)).strip(),
                    "description": str(info.get("description", "")).strip(),
                    "subcategories": info.get("subcategories", info.get("children", [])),
                })
            else:
                iterable.append({
                    "code": str(code).strip(),
                    "name": str(info).strip(),
                    "description": "",
                    "subcategories": [],
                })
    else:
        iterable = source

    for cat in iterable:
        if not isinstance(cat, dict):
            continue
        code = str(cat.get("code", "")).strip()
        if not code:
            continue
        children = cat.get("subcategories", cat.get("children", [])) or []
        yield {
            "code": code,
            "name": str(cat.get("name", code)).strip(),
            "description": str(cat.get("description", "")).strip(),
            "subcategories": [
                child_info for child_info in (
                    _normalize_subcategory(code, child) for child in children
                )
                if child_info["code"]
            ],
        }


def _schema_code_map() -> dict[str, dict]:
    code_to_info = {}
    for cat in iter_classification_schema():
        code_to_info[cat["code"]] = {
            "code": cat["code"],
            "name": cat["name"],
            "description": cat["description"],
        }
        for child in cat["subcategories"]:
            code_to_info[child["code"]] = child
    return code_to_info


class LibrarianService:
    def __init__(self):
        self.llm = LLMService()

    def classify_all(self, progress_cb=None, incremental=False) -> list[dict]:
        """分类知识条目。incremental=True 时只处理未分类的新条目，保留已有分类。"""
        if incremental:
            all_items = Database.list_knowledge(limit=10000)
            classified_ids = Database.get_all_classified_ids()
            # 找出"Z 未分类"对应的 category_id，其中的条目视为"未真正分类"，允许重新处理
            uncat_ids = self._get_uncategorized_item_ids()
            items = [it for it in all_items
                     if it["id"] not in classified_ids or it["id"] in uncat_ids]
        else:
            items = Database.list_knowledge(limit=10000)
        if not items:
            return []

        batch_size = Config.get("librarian.batch_size", 20)
        schema_text = get_schema_prompt()
        dynamic_text = self._get_dynamic_schema_text()
        # 预加载 DB 分类映射（避免工作线程中并发访问 SQLite）
        db_cats_snapshot = Database.get_all_categories() if Database.get_conn() else []
        valid_codes = get_all_codes(include_db=True)
        lock = threading.Lock()
        all_results = []
        processed_count = [0]  # 实际已处理条目数（非批次数 × batch_size）

        # 构建批次
        batches = []
        for i in range(0, len(items), batch_size):
            batches.append(items[i:i + batch_size])

        def process_batch(batch):
            items_text = "\n".join(
                f"{i+1}. [{item['title']}] (类型:{item.get('file_type', 'txt')}, "
                f"摘要:{_smart_summary(item.get('content') or '')})"
                for i, item in enumerate(batch)
            )
            prompt = CLASSIFY_PROMPT.format(schema=schema_text, dynamic_schema=dynamic_text, items=items_text, item_count=len(batch))
            try:
                llm = LLMService()
                response = llm.chat([{"role": "user", "content": prompt}], max_tokens_override=4096, silent=True)
                logging.debug(f"分类 LLM 返回 ({len(batch)} 条): {response[:500]}")
                result = self._parse_response(response, batch, db_cats_snapshot, valid_codes)
                # 诊断：哪些条目未被匹配
                diag_matched = set()
                for r in result:
                    diag_matched.update(r.get("item_ids", []))
                    for child in r.get("children", []):
                        diag_matched.update(child.get("item_ids", []))
                unmatched = [it for it in batch if it["id"] not in diag_matched]
                if unmatched:
                    logging.info(f"批次 {len(batch)} 条中 {len(unmatched)} 条未匹配: "
                                 f"{[it['title'][:20] for it in unmatched[:5]]}")
                return result
            except Exception as e:
                logging.warning(f"分类批次失败 ({len(batch)} 条): {e}")
                return []

        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = {pool.submit(process_batch, b): b for b in batches}
            failed_batches = []
            for future in as_completed(futures):
                batch = futures[future]
                result = future.result()
                if result:
                    with lock:
                        all_results.extend(result)
                else:
                    failed_batches.append(batch)
                # 进度回调在主线程中执行，避免工作线程写 DB 的竞态
                with lock:
                    processed_count[0] += len(batch)
                    if progress_cb:
                        progress_cb("分类中", min(processed_count[0], len(items)), len(items))

        # 对失败批次重试一次
        if failed_batches:
            logging.info(f"重试 {len(failed_batches)} 个失败批次...")
            for batch in failed_batches:
                try:
                    llm = LLMService()
                    items_text = "\n".join(
                        f"{i+1}. [{item['title']}] (类型:{item.get('file_type', 'txt')}, "
                        f"摘要:{_smart_summary(item.get('content') or '')})"
                        for i, item in enumerate(batch)
                    )
                    prompt = CLASSIFY_PROMPT.format(schema=schema_text, dynamic_schema=dynamic_text, items=items_text, item_count=len(batch))
                    response = llm.chat([{"role": "user", "content": prompt}], max_tokens_override=4096, silent=True)
                    result = self._parse_response(response, batch, db_cats_snapshot, valid_codes)
                    if result:
                        all_results.extend(result)
                except Exception as e:
                    logging.warning(f"重试批次仍然失败: {e}")

        # 二次补救：对 LLM 未匹配到的条目单独再分类一轮
        classified_ids_so_far = set()
        for r in all_results:
            classified_ids_so_far.update(r.get("item_ids", []))
            for child in r.get("children", []):
                classified_ids_so_far.update(child.get("item_ids", []))
        missed_items = [it for it in items if it["id"] not in classified_ids_so_far]
        if missed_items:
            logging.info(f"二次补救: {len(missed_items)} 条未匹配，单独重新分类...")
            # 分小批次逐个处理，确保每条都有机会被分类
            reclassify_batch = 5
            for i in range(0, len(missed_items), reclassify_batch):
                sub_batch = missed_items[i:i + reclassify_batch]
                try:
                    llm = LLMService()
                    items_text = "\n".join(
                        f"{j+1}. [{item['title']}] (类型:{item.get('file_type', 'txt')}, "
                        f"摘要:{_smart_summary(item.get('content') or '')})"
                        for j, item in enumerate(sub_batch)
                    )
                    prompt = RECLASSIFY_PROMPT.format(
                        schema=schema_text, dynamic_schema=dynamic_text,
                        items=items_text, item_count=len(sub_batch),
                    )
                    response = llm.chat([{"role": "user", "content": prompt}], max_tokens_override=4096, silent=True)
                    result = self._parse_response(response, sub_batch, db_cats_snapshot, valid_codes)
                    if result:
                        all_results.extend(result)
                except Exception as e:
                    logging.warning(f"二次补救批次失败: {e}")

        # 将未匹配到的条目归入未分类
        classified_ids = set()
        for r in all_results:
            classified_ids.update(r["item_ids"])
            for child in r.get("children", []):
                classified_ids.update(child.get("item_ids", []))
        unclassified = [item for item in items if item["id"] not in classified_ids]
        if unclassified:
            all_results.append({
                "code": UNCATEGORIZED["code"],
                "name": UNCATEGORIZED["name"],
                "description": UNCATEGORIZED["description"],
                "item_ids": [item["id"] for item in unclassified],
                "children": [],
            })

        # 写入数据库
        if incremental:
            # 增量模式：不清空已有分类，只追加新分类关系
            self._save_incremental(all_results)
        else:
            Database.clear_categories(keep_dynamic=True)
            self._save_with_schema(all_results)

        if progress_cb:
            progress_cb("完成", len(items), len(items))

        return all_results

    def generate_catalog(self) -> str:
        """生成 Markdown 格式的完整目录文档"""
        categories = Database.get_all_categories()
        if not categories:
            return "# 知识库目录\n\n(暂无分类，请先执行自动整理)"

        roots = [c for c in categories if not c["parent_id"]]

        lines = ["# 知识库目录\n"]
        total = 0

        for root in roots:
            root_items = Database.get_knowledge_by_category(root["id"])
            children = [c for c in categories if c["parent_id"] == root["id"]]

            # 跳过完全为空的大类
            root_count = len(root_items) + sum(
                len(Database.get_knowledge_by_category(c["id"])) for c in children
            )
            if root_count == 0:
                continue

            lines.append(f"\n## {root['name']}\n")
            if root["description"]:
                lines.append(f"> {root['description']}\n")

            if children:
                for child in children:
                    child_items = Database.get_knowledge_by_category(child["id"])
                    if not child_items:
                        continue
                    lines.append(f"\n### {child['name']}\n")
                    if child["description"]:
                        lines.append(f"> {child['description']}\n")
                    for item in child_items:
                        lines.append(f"- {item['title']}")
                        total += 1
                    lines.append("")

            for item in root_items:
                lines.append(f"- {item['title']}")
                total += 1

            lines.append("")

        lines.insert(1, f"共 {total} 条知识。\n")
        return "\n".join(lines)

    def _get_uncategorized_item_ids(self) -> set[str]:
        """返回归入'Z 未分类'的条目 ID — 这些条目虽然有关联记录但并未真正分类"""
        try:
            cats = Database.get_all_categories()
            for c in cats:
                if c["name"].startswith(UNCATEGORIZED["code"]):
                    items = Database.get_knowledge_by_category(c["id"])
                    return {it["id"] for it in items}
        except Exception:
            pass
        return set()

    def _generate_dynamic_code(self, name: str, existing_codes: set[str] | None = None) -> str:
        """为动态新分类生成唯一 code"""
        if existing_codes is None:
            existing_codes = get_all_codes(include_db=True)
        for i in range(1, 100):
            code = f"X{i}"
            if code not in existing_codes:
                return code
        return f"DYN-{uuid.uuid4().hex[:4]}"

    def _get_dynamic_schema_text(self) -> str:
        """获取 DB 中已有的动态分类文本，拼接到 prompt 中"""
        try:
            db_cats = Database.get_all_categories()
        except Exception:
            return ""
        schema_codes = get_all_codes()
        # 精确匹配：取分类名第一个空格前的 code 部分做判断
        dynamic = []
        for c in db_cats:
            parts = c["name"].split(" ", 1)
            code = parts[0] if parts else ""
            if code not in schema_codes:
                dynamic.append(c)
        if not dynamic:
            return ""
        lines = ["已创建的自定义分类："]
        for cat in dynamic:
            lines.append(f"- {cat['name']}" + (f"：{cat['description']}" if cat.get("description") else ""))
        return "\n".join(lines) + "\n\n"

    def _parse_response(self, response: str, batch: list[dict],
                        db_cats: list[dict] | None = None,
                        valid_codes: set[str] | None = None) -> list[dict]:
        """解析 LLM 返回的 JSON 归类结果，使用序号匹配"""
        text = response.strip()

        json_text = ""
        if "```json" in text:
            json_text = text.split("```json", 1)[-1].split("```", 1)[0]
        elif "```" in text:
            json_text = text.split("```", 1)[-1].rsplit("```", 1)[0]
        elif "[" in text and "]" in text:
            json_text = text[text.index("["):text.rindex("]") + 1]

        if not json_text.strip():
            logging.warning(f"分类: LLM 返回中未找到 JSON (response 长度={len(text)})")
            return []

        try:
            results = json.loads(json_text.strip())
        except json.JSONDecodeError as e:
            logging.warning(f"分类: JSON 解析失败: {e}, 文本片段: {json_text[:200]}")
            return []
        if not isinstance(results, list):
            logging.warning(f"分类: LLM 返回非列表类型: {type(results)}")
            return []

        if valid_codes is None:
            valid_codes = get_all_codes(include_db=True)

        # 序号(1-based) -> batch index(0-based) -> item id
        idx_to_id = {i: batch[i]["id"] for i in range(len(batch))}
        matched_ids = set()

        # 构建 code -> schema 信息的映射
        code_to_info = _schema_code_map()

        # 加载 DB 中动态分类的映射
        if db_cats:
            for cat in db_cats:
                parts = cat["name"].split(" ", 1)
                code = parts[0] if parts else ""
                if code and code not in code_to_info:
                    code_to_info[code] = {
                        "code": code,
                        "name": parts[1] if len(parts) > 1 else cat["name"],
                        "description": cat.get("description", ""),
                        "parent_code": None,
                        "db_id": cat["id"],
                    }
                    if cat.get("parent_id"):
                        parent = next((c for c in db_cats if c["id"] == cat["parent_id"]), None)
                        if parent:
                            pparts = parent["name"].split(" ", 1)
                            code_to_info[code]["parent_code"] = pparts[0] if pparts else None

        # 兼容旧格式：如果 LLM 返回的是 items（标题列表）而非 ids（序号列表），
        # 仍然尝试标题匹配
        def resolve_item_ids(entry: dict) -> list[str]:
            """从 LLM 返回的 entry 中解析出 item id 列表"""
            ids_found = []

            # 优先使用 ids 字段（序号，1-based）
            raw_ids = entry.get("ids", [])
            if raw_ids:
                for rid in raw_ids:
                    try:
                        idx = int(rid) - 1
                        if 0 <= idx < len(batch) and idx_to_id[idx] not in matched_ids:
                            ids_found.append(idx_to_id[idx])
                    except (ValueError, TypeError):
                        pass
                return ids_found

            # 回退：使用 items 字段（标题列表）做模糊匹配
            raw_items = entry.get("items", [])
            if not raw_items:
                return []

            title_to_ids: dict[str, list[int]] = {}
            for i, item in enumerate(batch):
                title_to_ids.setdefault(item["title"], []).append(i)

            def _normalize(s: str) -> str:
                s = s.strip()
                s = s.replace("（", "(").replace("）", ")")
                s = s.replace("【", "[").replace("】", "]")
                s = s.replace("《", "<").replace("》", ">")
                while s and s[-1] in "。、，：；！？,.:;!?·— ":
                    s = s[:-1]
                return s.strip()

            for title in raw_items:
                # 精确匹配
                matched = False
                for orig_title, idxs in title_to_ids.items():
                    for idx in idxs:
                        if idx_to_id[idx] not in matched_ids and orig_title == title:
                            ids_found.append(idx_to_id[idx])
                            matched = True
                            break
                    if matched:
                        break
                if matched:
                    continue
                # 归一化匹配
                norm = _normalize(title)
                for orig_title, idxs in title_to_ids.items():
                    if _normalize(orig_title) == norm:
                        for idx in idxs:
                            if idx_to_id[idx] not in matched_ids:
                                ids_found.append(idx_to_id[idx])
                                matched = True
                                break
                        break
                if matched:
                    continue
                # 模糊匹配
                orig_titles = list(title_to_ids.keys())
                matches = difflib.get_close_matches(title, orig_titles, n=1, cutoff=0.6)
                if matches:
                    for idx in title_to_ids[matches[0]]:
                        if idx_to_id[idx] not in matched_ids:
                            ids_found.append(idx_to_id[idx])
                            break
            return ids_found

        parsed = []
        for entry in results:
            if not isinstance(entry, dict):
                continue
            code = entry.get("code", "").strip().upper()
            is_new = entry.get("is_new", False)
            if code == "NEW":
                is_new = True

            item_ids = resolve_item_ids(entry)
            if item_ids:
                matched_ids.update(item_ids)
            if not item_ids:
                continue

            if is_new:
                # LLM 创建的新分类
                new_name = entry.get("new_name", "新分类").strip()
                new_desc = entry.get("new_description", "").strip()
                parent_code = entry.get("parent_code")
                # 生成唯一 code
                new_code = self._generate_dynamic_code(new_name, valid_codes)
                parsed.append({
                    "code": new_code,
                    "name": new_name,
                    "description": new_desc,
                    "item_ids": item_ids,
                    "children": [],
                    "is_dynamic": True,
                    "parent_code": parent_code if parent_code in valid_codes else None,
                })
                continue

            if code not in valid_codes:
                continue

            info = code_to_info.get(code, {})
            parent_code = info.get("parent_code")

            if parent_code:
                parent_info = code_to_info.get(parent_code, {})
                parsed.append({
                    "code": parent_code,
                    "name": parent_info.get("name", parent_code),
                    "description": parent_info.get("description", ""),
                    "item_ids": [],
                    "children": [{
                        "code": code,
                        "name": info.get("name", code),
                        "description": info.get("description", ""),
                        "item_ids": item_ids,
                    }],
                })
            else:
                parsed.append({
                    "code": code,
                    "name": info.get("name", code),
                    "description": info.get("description", ""),
                    "item_ids": item_ids,
                    "children": [],
                })

        # 合并同 code 的条目
        merged = {}
        for p in parsed:
            code = p["code"]
            if code not in merged:
                merged[code] = {k: v for k, v in p.items()}
                merged[code]["item_ids"] = list(p["item_ids"])
                merged[code]["children"] = list(p.get("children", []))
            else:
                merged[code]["item_ids"].extend(p["item_ids"])
                merged[code]["children"].extend(p.get("children", []))

        for m in merged.values():
            child_map = {}
            for child in m["children"]:
                cc = child["code"]
                if cc not in child_map:
                    child_map[cc] = child
                else:
                    child_map[cc]["item_ids"].extend(child["item_ids"])
            m["children"] = list(child_map.values())

        return list(merged.values())

    def _save_incremental(self, results: list[dict]):
        """增量模式：只追加新分类关系，不清空已有分类，不重建分类树"""
        existing_cats = Database.get_all_categories()
        if not existing_cats:
            Database.clear_categories()
            self._save_with_schema(results)
            return

        # 构建 code -> db_id 映射
        code_to_db_id = {}
        schema_codes = set(_schema_code_map())
        for cat in existing_cats:
            name = cat["name"]
            parts = name.split(" ", 1)
            code = parts[0] if parts else ""
            if code in schema_codes:
                code_to_db_id[code] = cat["id"]
            if name.startswith(UNCATEGORIZED["code"]):
                code_to_db_id[UNCATEGORIZED["code"]] = cat["id"]
            # 动态分类也加入映射
            if parts and parts[0] not in code_to_db_id:
                code_to_db_id[parts[0]] = cat["id"]

        # 先处理动态新分类：创建不存在的分类
        for r in results:
            if not r.get("is_dynamic"):
                continue
            code = r["code"]
            if code in code_to_db_id:
                continue
            # 查找 parent
            parent_id = None
            parent_code = r.get("parent_code")
            if parent_code and parent_code in code_to_db_id:
                parent_id = code_to_db_id[parent_code]
            cat_id = str(uuid.uuid4())
            Database.insert_category(
                cat_id,
                f"{code} {r['name']}",
                r.get("description", ""),
                parent_id=parent_id,
            )
            code_to_db_id[code] = cat_id

        uncat_db_id = code_to_db_id.get(UNCATEGORIZED["code"])

        def assign_replacing_uncategorized(item_id: str, cat_db_id: str):
            if uncat_db_id and cat_db_id != uncat_db_id:
                Database.unassign_category(item_id, uncat_db_id)
            Database.assign_category(item_id, cat_db_id)

        # 写入条目-分类关联
        for r in results:
            code = r.get("code", "")
            if r.get("item_ids"):
                cat_db_id = code_to_db_id.get(code)
                if cat_db_id:
                    for item_id in r["item_ids"]:
                        assign_replacing_uncategorized(item_id, cat_db_id)
            for child in r.get("children", []):
                child_code = child.get("code", "")
                child_db_id = code_to_db_id.get(child_code)
                if child_db_id:
                    for item_id in child.get("item_ids", []):
                        assign_replacing_uncategorized(item_id, child_db_id)

    def _save_with_schema(self, results: list[dict]):
        """按预设分类结构写入数据库，保留完整分类树，同时写入动态新分类"""
        code_to_db_id = {}

        # 加载 DB 中残留的动态分类（keep_dynamic=True 时可能存在）
        existing_cats = Database.get_all_categories()
        for c in existing_cats:
            parts = c["name"].split(" ", 1)
            code = parts[0] if parts else ""
            if code and code not in code_to_db_id:
                code_to_db_id[code] = c["id"]

        # 先写入所有预设大类
        for cat_info in iter_classification_schema():
            cat_code = cat_info["code"]
            cat_id = str(uuid.uuid4())
            code_to_db_id[cat_code] = cat_id
            Database.insert_category(cat_id, f"{cat_code} {cat_info['name']}", cat_info.get("description", ""))

            for child in cat_info.get("subcategories", []):
                child_code, child_name = child["code"], child["name"]
                sub_id = str(uuid.uuid4())
                code_to_db_id[child_code] = sub_id
                Database.insert_category(sub_id, f"{child_code} {child_name}", child.get("description", ""), parent_id=cat_id)

        # 写入未分类
        z_id = str(uuid.uuid4())
        code_to_db_id[UNCATEGORIZED["code"]] = z_id
        Database.insert_category(z_id, f"{UNCATEGORIZED['code']} {UNCATEGORIZED['name']}", UNCATEGORIZED["description"])

        # 写入动态新分类（跳过 DB 中已存在的）
        for r in results:
            if not r.get("is_dynamic"):
                continue
            code = r["code"]
            if code in code_to_db_id:
                continue
            parent_id = None
            parent_code = r.get("parent_code")
            if parent_code and parent_code in code_to_db_id:
                parent_id = code_to_db_id[parent_code]
            dyn_id = str(uuid.uuid4())
            code_to_db_id[code] = dyn_id
            Database.insert_category(
                dyn_id,
                f"{code} {r['name']}",
                r.get("description", ""),
                parent_id=parent_id,
            )

        # 将归类结果关联到对应类别
        for r in results:
            code = r.get("code", "")
            if r.get("item_ids"):
                cat_db_id = code_to_db_id.get(code)
                if cat_db_id:
                    for item_id in r["item_ids"]:
                        Database.assign_category(item_id, cat_db_id)
            for child in r.get("children", []):
                child_code = child.get("code", "")
                child_db_id = code_to_db_id.get(child_code)
                if child_db_id:
                    for item_id in child.get("item_ids", []):
                        Database.assign_category(item_id, child_db_id)
