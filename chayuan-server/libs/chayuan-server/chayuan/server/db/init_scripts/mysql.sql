-- 当「主业务数据库」配置验证失败且错误为 "Unknown database" 时，
-- 配置面板 / 保存流程会以无 schema 形式连上 MySQL/MariaDB 服务器并执行这段 SQL。
--
-- 占位符：
--   {{database}} — 要创建的业务库名（Python 侧用 identifier 白名单校验）。
--
-- 行为：
--   - 字符集 utf8mb4 + utf8mb4_unicode_ci；确保 emoji / 生僻字能正确存储；
--     utf8 已在 MySQL 5.7 起被官方弃用，不要再用。
--   - 使用 IF NOT EXISTS 保证幂等；再次跑也不会报错。
CREATE DATABASE IF NOT EXISTS `{{database}}`
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;
