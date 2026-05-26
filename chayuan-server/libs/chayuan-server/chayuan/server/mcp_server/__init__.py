"""察元 MCP Server（N-8）——把 Text2SQL / 多源检索 / KB 问答 暴露为 MCP 工具。

MCP (Model Context Protocol) 是 Anthropic 2024 开源的标准协议，让 Claude Desktop /
Cursor / Continue 等客户端**直接调用**我们的能力而无需懂察元 HTTP API。

启动方式：
  # 独立进程（stdio transport，典型 Claude Desktop 配置）
  python -m chayuan.server.mcp_server stdio

  # SSE 传输（网络访问）
  python -m chayuan.server.mcp_server sse --host 0.0.0.0 --port 7862

在 Claude Desktop config 添加：
  "chayuan": {
      "command": "python",
      "args": ["-m", "chayuan.server.mcp_server", "stdio"]
  }

暴露的工具：
- chayuan_kb_search(kb, query)       → 检索向量知识库，返回 markdown chunks
- chayuan_multi_source_search(query) → 多源并行（SQL/Mongo/ES/Vector）
- chayuan_text2sql(source_id, query) → Text2SQL 跑在指定 SQL 数据源
- chayuan_pii_scan(text)             → PII 扫描 + 脱敏（合规工具）

**fail-open**：`mcp` 包未装时，导入本模块不抛；主服务不受影响。
"""

__all__ = ["create_server", "run_stdio", "run_sse"]


def create_server():
    from chayuan.server.mcp_server.server import create_server as _c
    return _c()


def run_stdio():
    from chayuan.server.mcp_server.server import run_stdio as _r
    _r()


def run_sse(host: str = "127.0.0.1", port: int = 7862):
    from chayuan.server.mcp_server.server import run_sse as _r
    _r(host=host, port=port)
