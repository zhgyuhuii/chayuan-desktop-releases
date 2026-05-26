-- 当「主业务数据库」配置验证失败且错误为 "database does not exist" 时，
-- 配置面板 / 保存流程会连到 PostgreSQL 的管理库（默认 postgres）并执行这段 SQL。
--
-- 占位符：
--   {{database}} — 要创建的业务库名（已在 Python 侧用 identifier 校验，避免注入）。
--
-- 行为：
--   - 使用 template0 + UTF8 编码，避免宿主 locale 非 C.UTF-8 时失败；
--   - 与 sqlalchemy/psycopg2 的 UTF-8 连接保持一致，应对 emoji / 中文无压力；
--   - OWNER 默认跟随当前连接角色，无需额外授权。
--
-- 注意：``CREATE DATABASE`` 不能在事务里执行，Python 侧会把连接切成
-- AUTOCOMMIT 隔离级别再发这条语句。
CREATE DATABASE "{{database}}"
    WITH ENCODING 'UTF8'
    TEMPLATE template0;
