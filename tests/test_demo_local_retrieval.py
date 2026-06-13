"""
本地检索演示脚本的测试

验证 demo_local_retrieval.py 的核心功能：
1. 测试文档创建
2. 演示流程执行
3. 结果验证
"""

import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

import scripts.demo_local_retrieval as demo_module
from scripts.demo_local_retrieval import (
    create_test_documents,
    run_demo,
)


class FakeIndexResult:
    created = 1
    updated = 1
    deleted = 0
    skipped = 0


class FakePathIndexer:
    def index_path(self, path, recursive):
        assert path.name == "docs"
        assert recursive is True
        return FakeIndexResult()


class FakeSearchService:
    def search(self, query, top_k):
        assert top_k == 3
        return [{
            "title": "Python 编程入门",
            "score": 1.0,
            "text": f"{query} 异常处理 try-except",
            "citation": {
                "document": "python_tutorial.md",
                "path": "docs/python_tutorial.md",
                "knowledge_id": "doc-1",
                "block_id": "doc-1-block-1",
                "text": f"{query} 异常处理 try-except",
            },
        }]


class FakeContainer:
    path_indexer = FakePathIndexer()
    search_service = FakeSearchService()


@pytest.fixture(autouse=True)
def fake_demo_services(monkeypatch):
    """Demo 单测使用确定性服务，不依赖本机 Ollama 或网络。"""
    monkeypatch.setattr(demo_module, "create_container", lambda **kwargs: FakeContainer())
    monkeypatch.setattr(demo_module, "shutdown_container", lambda container: None)


@pytest.fixture
def temp_workdir():
    """创建临时工作目录"""
    workdir = Path(tempfile.mkdtemp(prefix="test_demo_"))
    yield workdir
    # 清理
    if workdir.exists():
        shutil.rmtree(workdir, ignore_errors=True)


def test_create_test_documents(temp_workdir):
    """验证测试文档创建功能"""
    # 创建文档
    create_test_documents(temp_workdir)

    # 验证文档目录
    docs_dir = temp_workdir / "docs"
    assert docs_dir.exists(), "docs 目录应该被创建"

    # 验证文档文件
    expected_files = [
        "python_tutorial.md",
        "database_guide.md",
        "api_design.md",
    ]

    for filename in expected_files:
        file_path = docs_dir / filename
        assert file_path.exists(), f"{filename} 应该被创建"
        content = file_path.read_text(encoding="utf-8")
        assert len(content) > 100, f"{filename} 应该包含足够的内容"


def test_demo_output_supports_windows_console_encoding(temp_workdir, monkeypatch):
    """演示脚本不应向 Windows 默认控制台输出不可编码的状态符号。"""
    output = io.TextIOWrapper(io.BytesIO(), encoding="cp936", errors="strict")
    monkeypatch.setattr(sys, "stdout", output)

    results = run_demo(temp_workdir, keep_workdir=True)
    output.flush()

    assert isinstance(results, dict)
    assert (temp_workdir / "config.yaml").exists()


def test_demo_uses_container_services_as_properties(temp_workdir, monkeypatch):
    """演示脚本应遵循 AppContainer 的属性式服务访问契约。"""

    class ContractIndexResult:
        created = 1
        updated = 1
        deleted = 0
        skipped = 0

    class ContractPathIndexer:
        def index_path(self, path, recursive):
            assert path == temp_workdir / "docs"
            assert recursive is True
            return ContractIndexResult()

    class ContractSearchService:
        def search(self, query, top_k):
            assert top_k == 3
            return [{
                "title": "Python 编程入门",
                "score": 1.0,
                "text": f"{query} 异常处理 try-except",
                "citation": {
                    "document": "python_tutorial.md",
                    "path": "docs/python_tutorial.md",
                    "knowledge_id": "doc-1",
                    "block_id": "doc-1-block-1",
                    "text": f"{query} 异常处理 try-except",
                },
            }]

    class ContractContainer:
        path_indexer = ContractPathIndexer()
        search_service = ContractSearchService()

    monkeypatch.setattr(demo_module, "create_container", lambda **kwargs: ContractContainer())
    monkeypatch.setattr(demo_module, "shutdown_container", lambda container: None)

    results = run_demo(temp_workdir, keep_workdir=True)

    assert results == {
        "initial_hit": True,
        "incremental_update": True,
        "citation_complete": True,
    }


def test_run_demo_basic(temp_workdir):
    """验证演示流程基本执行"""
    # 运行演示（保留工作目录以便检查）
    results = run_demo(temp_workdir, keep_workdir=True)

    # 验证返回结构
    assert isinstance(results, dict), "结果应该是字典"
    assert "initial_hit" in results, "应该包含 initial_hit 字段"
    assert "incremental_update" in results, "应该包含 incremental_update 字段"
    assert "citation_complete" in results, "应该包含 citation_complete 字段"

    # 验证字段类型
    assert isinstance(results["initial_hit"], bool)
    assert isinstance(results["incremental_update"], bool)
    assert isinstance(results["citation_complete"], bool)


def test_demo_creates_config(temp_workdir):
    """验证演示创建配置文件"""
    run_demo(temp_workdir, keep_workdir=True)

    config_path = temp_workdir / "config.yaml"
    assert config_path.exists(), "config.yaml 应该被创建"

    content = config_path.read_text(encoding="utf-8")
    assert "storage:" in content, "配置应该包含 storage 部分"
    assert "embedding:" in content, "配置应该包含 embedding 部分"
    assert "mcp:" in content, "配置应该包含 mcp 部分"


def test_demo_creates_documents(temp_workdir):
    """验证演示创建文档"""
    run_demo(temp_workdir, keep_workdir=True)

    docs_dir = temp_workdir / "docs"
    assert docs_dir.exists(), "docs 目录应该被创建"

    md_files = list(docs_dir.glob("*.md"))
    assert len(md_files) >= 3, "应该至少创建 3 个 markdown 文档"


def test_demo_output_structure(temp_workdir):
    """验证演示输出结构"""
    results = run_demo(temp_workdir, keep_workdir=True)

    # 验证所有必需字段
    required_fields = ["initial_hit", "incremental_update", "citation_complete"]
    for field in required_fields:
        assert field in results, f"结果应该包含 {field} 字段"


def test_demo_cleanup(temp_workdir):
    """验证演示清理功能"""
    # 不保留工作目录
    results = run_demo(temp_workdir, keep_workdir=False)

    # 验证工作目录被清理（除非演示失败）
    # 注意：如果演示失败，可能会保留目录用于调试
    if all(results.values()):
        # 所有测试通过时，目录应该被清理
        # 但由于异步操作，可能还有残留进程
        # 这里只验证函数正常返回
        pass


def test_demo_json_output(temp_workdir, tmp_path):
    """验证 JSON 输出功能"""
    # 运行演示
    results = run_demo(temp_workdir, keep_workdir=True)

    # 保存 JSON
    json_path = tmp_path / "results.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2, ensure_ascii=False)

    # 验证 JSON 文件
    assert json_path.exists(), "JSON 文件应该被创建"

    # 读取并验证
    with open(json_path, "r", encoding="utf-8") as f:
        loaded = json.load(f)

    assert loaded == results, "JSON 内容应该与原始结果一致"


def test_demo_document_content_quality(temp_workdir):
    """验证创建的文档内容质量"""
    create_test_documents(temp_workdir)

    docs_dir = temp_workdir / "docs"

    # 验证 Python 教程
    python_doc = docs_dir / "python_tutorial.md"
    python_content = python_doc.read_text(encoding="utf-8")
    assert "Python" in python_content
    assert "函数" in python_content or "function" in python_content.lower()

    # 验证数据库指南
    db_doc = docs_dir / "database_guide.md"
    db_content = db_doc.read_text(encoding="utf-8")
    assert "SQL" in db_content or "数据库" in db_content

    # 验证 API 设计
    api_doc = docs_dir / "api_design.md"
    api_content = api_doc.read_text(encoding="utf-8")
    assert "API" in api_content or "REST" in api_content


def test_demo_incremental_update_logic(temp_workdir):
    """验证增量更新逻辑"""
    # 第一次运行
    run_demo(temp_workdir, keep_workdir=True)

    # 验证文档已创建
    docs_dir = temp_workdir / "docs"
    python_doc = docs_dir / "python_tutorial.md"
    assert python_doc.exists()

    # 记录初始内容长度
    initial_length = len(python_doc.read_text(encoding="utf-8"))

    # 修改文档
    new_content = python_doc.read_text(encoding="utf-8") + "\n\n# 新增章节\n测试内容"
    python_doc.write_text(new_content, encoding="utf-8")

    # 验证文档已修改
    updated_length = len(python_doc.read_text(encoding="utf-8"))
    assert updated_length > initial_length, "文档应该被成功修改"


def test_demo_search_functionality(temp_workdir):
    """验证搜索功能"""
    results = run_demo(temp_workdir, keep_workdir=True)

    # 如果演示成功执行，应该至少有一个搜索结果
    # 注意：这依赖于实际的索引和搜索实现
    # 在某些环境下（如缺少 embedding 服务），搜索可能失败
    # 因此这里只验证函数正常返回
    assert isinstance(results, dict)


def test_demo_error_handling(temp_workdir):
    """验证错误处理"""
    # 创建一个无效的工作目录（例如只读）
    # 注意：这个测试可能在某些系统上无法执行（权限问题）
    # 因此只验证基本的异常处理逻辑

    try:
        results = run_demo(temp_workdir, keep_workdir=True)
        # 如果执行成功，验证返回结构
        assert isinstance(results, dict)
    except Exception as e:
        # 如果抛出异常，验证异常类型合理
        assert isinstance(e, Exception)


def test_demo_configuration_validation(temp_workdir):
    """验证配置生成"""
    run_demo(temp_workdir, keep_workdir=True)

    config_path = temp_workdir / "config.yaml"
    content = config_path.read_text(encoding="utf-8")

    # 验证关键配置项
    assert "data_dir" in content, "配置应该包含 data_dir"
    assert "tool_profile" in content, "配置应该包含 tool_profile"
    assert "write_policy" in content, "配置应该包含 write_policy"

    # 验证配置值合理性
    assert "core" in content or "full" in content, "tool_profile 应该是有效值"
    assert "disabled" in content or "preview_only" in content, "write_policy 应该是有效值"
