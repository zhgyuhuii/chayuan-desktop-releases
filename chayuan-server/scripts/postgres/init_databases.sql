-- 察元AI助手服务（Chayuan）：PostgreSQL 建库脚本
--
-- 使用方式（示例，按本机用户/主机修改）：
--   psql -h 127.0.0.1 -p 5432 -U root -d postgres -v ON_ERROR_STOP=1 -f scripts/postgres/init_databases.sql
--
-- 说明：
--   1) 库名 chayuan 对应 chayuan_data/basic_settings.yaml 中 SQLALCHEMY_DATABASE_URI 的库名。
--   2) 若使用 PG 向量库（kb_settings.yaml 里 DEFAULT_VS_TYPE: pg），需有对应 connection_uri 中的库，
--      并在该库执行 scripts/postgres/install_pgvector.sql。
--   3) 若库已存在，CREATE DATABASE 会失败，可忽略或先 DROP DATABASE（注意数据丢失）。
--   4) OWNER 请改为实际业务用户（示例为 root，与常见本机配置一致）。

CREATE DATABASE chayuan
  OWNER root
  ENCODING 'UTF8'
  TEMPLATE template0;

-- 可选：PG 向量库专用（与 kb_settings.yaml 中 kbs_config.pg.connection_uri 的库名保持一致）
-- CREATE DATABASE chayuan_vector
--   OWNER root
--   ENCODING 'UTF8'
--   TEMPLATE template0;
