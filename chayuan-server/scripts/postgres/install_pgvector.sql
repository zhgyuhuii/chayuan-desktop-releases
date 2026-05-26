-- 在「PG 向量库」所使用的数据库内执行（与 kb_settings.yaml → kbs_config.pg.connection_uri 指向的库一致）。
--
-- 示例：
--   psql -h 127.0.0.1 -p 5432 -U root -d chayuan -f scripts/postgres/install_pgvector.sql
--   若 URI 指向 postgres 库，则把 -d 改为 postgres。
--
-- 需本机已安装 pgvector 扩展包（如 macOS: brew install pgvector）。

CREATE EXTENSION IF NOT EXISTS vector;
