#!/usr/bin/env python3
"""
本地检索功能演示脚本

演示完整的本地检索工作流：
1. 初始化配置
2. 创建测试文档
3. 索引文档
4. 搜索查询
5. 修改文档
6. 增量更新
7. 再次搜索验证新内容

使用方法:
    python scripts/demo_local_retrieval.py [--workdir PATH] [--keep]
"""

import argparse
import json
import shutil
import sys
import tempfile
from pathlib import Path

# 添加项目根目录到 Python 路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from src.core.container import create_container, shutdown_container  # noqa: E402


def has_complete_citation(result: dict) -> bool:
    """检查搜索结果是否包含可追溯的统一 Citation。"""
    citation = result.get("citation")
    if not isinstance(citation, dict):
        return False
    required_fields = ("path", "knowledge_id", "block_id", "text")
    return all(citation.get(field) for field in required_fields)


def create_test_documents(workdir: Path) -> None:
    """创建测试文档"""
    docs_dir = workdir / "docs"
    docs_dir.mkdir(exist_ok=True)

    # 文档1: Python 教程
    (docs_dir / "python_tutorial.md").write_text(
        """# Python 编程入门

## 基础语法

Python 是一种简洁、易读的编程语言。它使用缩进来表示代码块。

### 变量和数据类型

```python
name = "Alice"  # 字符串
age = 30        # 整数
height = 1.65   # 浮点数
is_student = False  # 布尔值
```

### 控制流

```python
if age >= 18:
    print("成年人")
else:
    print("未成年人")

for i in range(5):
    print(i)
```

## 函数定义

```python
def greet(name: str) -> str:
    return f"Hello, {name}!"
```

## 类定义

```python
class Person:
    def __init__(self, name: str, age: int):
        self.name = name
        self.age = age

    def introduce(self):
        return f"My name is {self.name}, I'm {self.age} years old."
```
""",
        encoding="utf-8",
    )

    # 文档2: 数据库知识
    (docs_dir / "database_guide.md").write_text(
        """# 数据库使用指南

## SQL 基础

SQL (Structured Query Language) 是用于管理关系型数据库的标准语言。

### 创建表

```sql
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    email TEXT UNIQUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

### 插入数据

```sql
INSERT INTO users (name, email) VALUES ('Alice', 'alice@example.com');
```

### 查询数据

```sql
SELECT * FROM users WHERE age > 18 ORDER BY created_at DESC;
```

## NoSQL 数据库

NoSQL 数据库不使用传统的表格关系模型。

### MongoDB (文档数据库)

```javascript
db.users.insertOne({
    name: "Bob",
    age: 25,
    hobbies: ["reading", "gaming"]
});
```

### Redis (键值数据库)

```
SET user:1001 '{"name": "Charlie", "age": 30}'
GET user:1001
```

## 索引优化

索引可以显著提高查询性能，但会增加写入开销。

```sql
CREATE INDEX idx_users_email ON users(email);
```
""",
        encoding="utf-8",
    )

    # 文档3: API 设计
    (docs_dir / "api_design.md").write_text(
        """# RESTful API 设计指南

## HTTP 方法

- GET: 获取资源
- POST: 创建资源
- PUT: 更新资源（完整替换）
- PATCH: 部分更新资源
- DELETE: 删除资源

## URL 设计原则

使用名词复数形式：

```
GET    /api/v1/users          # 获取用户列表
GET    /api/v1/users/123      # 获取单个用户
POST   /api/v1/users          # 创建用户
PUT    /api/v1/users/123      # 更新用户
DELETE /api/v1/users/123      # 删除用户
```

## 状态码

- 200 OK: 请求成功
- 201 Created: 资源创建成功
- 400 Bad Request: 请求参数错误
- 401 Unauthorized: 未认证
- 403 Forbidden: 无权限
- 404 Not Found: 资源不存在
- 500 Internal Server Error: 服务器错误

## 认证方式

### JWT Token

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

### API Key

```
X-API-Key: your-api-key-here
```
""",
        encoding="utf-8",
    )


def run_demo(workdir: Path, keep_workdir: bool = False) -> dict:
    """运行演示流程"""
    container = None
    results = {
        "initial_hit": False,
        "incremental_update": False,
        "citation_complete": False,
    }

    try:
        print("=" * 60)
        print("本地检索功能演示")
        print("=" * 60)

        # 步骤 1: 创建测试文档
        print("\n[1/7] 创建测试文档...")
        create_test_documents(workdir)
        docs_dir = workdir / "docs"
        print(f"[OK] 创建了 {len(list(docs_dir.glob('*.md')))} 个测试文档")

        # 步骤 2: 初始化配置
        print("\n[2/7] 初始化配置...")
        config_path = workdir / "config.yaml"

        # 创建最小配置
        config_content = f"""
storage:
  data_dir: {workdir / 'data'}

embedding:
  provider: ollama
  model: nomic-embed-text
  base_url: http://localhost:11434/v1

llm:
  provider: ollama
  model: qwen2.5:7b
  base_url: http://localhost:11434/v1

rag:
  chunk_size: 500
  chunk_overlap: 50
  top_k: 5
  search_mode: hybrid

mcp:
  tool_profile: extended
  write_policy: disabled
"""
        config_path.write_text(config_content, encoding="utf-8")
        print(f"[OK] 配置已写入: {config_path}")

        # 加载配置并创建容器
        container = create_container(config_path=str(config_path))

        # 步骤 3: 索引文档
        print("\n[3/7] 索引文档...")
        path_indexer = container.path_indexer
        index_result = path_indexer.index_path(docs_dir, recursive=True)
        print(f"[OK] 索引完成: +{index_result.created} 新增, "
              f"~{index_result.updated} 更新, -{index_result.deleted} 删除, "
              f"跳过 {index_result.skipped}")

        # 步骤 4: 搜索查询
        print("\n[4/7] 搜索查询: 'Python 函数定义'")
        search_service = container.search_service
        search_results = search_service.search("Python 函数定义", top_k=3)

        if search_results and len(search_results) > 0:
            top_result = search_results[0]
            print(f"[OK] 找到 {len(search_results)} 个结果")
            print(f"  最佳匹配: {top_result.get('title', 'N/A')}")
            print(f"  相关度: {top_result.get('score', 0):.3f}")
            results["initial_hit"] = True

            # 检查统一 Citation 契约
            if any(has_complete_citation(result) for result in search_results):
                results["citation_complete"] = True
                print("  [OK] 引用完整: 包含内容和来源")
        else:
            print("[FAIL] 未找到结果")

        # 步骤 5: 修改文档
        print("\n[5/7] 修改文档 (添加新内容)...")
        python_doc = docs_dir / "python_tutorial.md"
        existing_content = python_doc.read_text(encoding="utf-8")

        new_section = """

## 新增章节: 异常处理

Python 使用 try-except 语句处理异常:

```python
try:
    result = 10 / 0
except ZeroDivisionError as e:
    print(f"错误: {e}")
finally:
    print("清理资源")
```

常见异常类型:
- ValueError: 值错误
- TypeError: 类型错误
- KeyError: 字典键不存在
- IndexError: 索引超出范围
"""
        python_doc.write_text(existing_content + new_section, encoding="utf-8")
        print("[OK] 文档已更新")

        # 步骤 6: 增量更新
        print("\n[6/7] 增量更新索引...")
        update_result = path_indexer.index_path(docs_dir, recursive=True)
        updated_count = update_result.updated
        print(f"[OK] 增量更新完成: +{update_result.created} 新增, "
              f"~{update_result.updated} 更新, -{update_result.deleted} 删除")

        index_updated = updated_count > 0 or update_result.created > 0

        # 步骤 7: 再次搜索验证新内容
        print("\n[7/7] 搜索新内容: '异常处理 try-except'")
        new_results = search_service.search("异常处理 try-except", top_k=3)

        if new_results and len(new_results) > 0:
            top_new = new_results[0]
            print(f"[OK] 找到 {len(new_results)} 个结果")
            print(f"  最佳匹配: {top_new.get('title', 'N/A')}")
            print(f"  相关度: {top_new.get('score', 0):.3f}")

            # 验证是否包含新内容
            citation = top_new.get("citation") or {}
            content = top_new.get("text", "") or citation.get("text", "")
            if "异常处理" in content or "try-except" in content:
                print("  [OK] 成功检索到新增内容")
                results["incremental_update"] = index_updated
        else:
            print("[FAIL] 未找到新内容")

        print("\n" + "=" * 60)
        print("演示结果:")
        print(f"  初始搜索命中: {'PASS' if results['initial_hit'] else 'FAIL'}")
        print(f"  增量更新成功: {'PASS' if results['incremental_update'] else 'FAIL'}")
        print(f"  引用完整性: {'PASS' if results['citation_complete'] else 'FAIL'}")
        print("=" * 60)

        return results

    except Exception as e:
        print(f"\n[FAIL] 演示失败: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        return results

    finally:
        # 清理资源
        try:
            if container is not None:
                shutdown_container(container)
        except Exception:
            pass

        # 清理工作目录（除非指定保留）
        if not keep_workdir and workdir.exists():
            print(f"\n清理工作目录: {workdir}")
            shutil.rmtree(workdir, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description="本地检索功能演示")
    parser.add_argument(
        "--workdir",
        type=Path,
        help="工作目录路径 (默认: 临时目录)",
    )
    parser.add_argument(
        "--keep",
        action="store_true",
        help="保留工作目录（用于调试）",
    )
    parser.add_argument(
        "--json-output",
        type=Path,
        help="输出结果到 JSON 文件",
    )

    args = parser.parse_args()

    # 创建工作目录
    if args.workdir:
        workdir = args.workdir
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        workdir = Path(tempfile.mkdtemp(prefix="shinehe_demo_"))

    print(f"工作目录: {workdir}\n")

    # 运行演示
    results = run_demo(workdir, keep_workdir=args.keep)

    # 输出 JSON
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        with open(args.json_output, "w", encoding="utf-8") as f:
            json.dump(results, f, indent=2, ensure_ascii=False)
        print(f"\n结果已保存到: {args.json_output}")

    # 返回退出码
    all_passed = all(results.values())
    sys.exit(0 if all_passed else 1)


if __name__ == "__main__":
    main()
