/**
 * 主流数据库官方 logo(simple-icons CC0)+ 品牌色 mapping。
 *
 * simple-icons 的 SVG 不指定 fill,可注入 ``fill="currentColor"`` 用 CSS
 * color 控品牌色;尺寸 24x24 viewBox。
 *
 * 涵盖:MySQL / PostgreSQL / SQLite / MongoDB / Elasticsearch / Redis /
 *      MariaDB / ClickHouse。
 *
 * SQL Server / Oracle 在 simple-icons 收录范围外(简体 / Oracle 的 brand
 * guidelines 限制 CC0 收录),走 ``fallbackText`` 文字 chip 路径。
 */
import mysqlSvg from '../../assets/db-logos/mysql.svg?raw';
import postgresqlSvg from '../../assets/db-logos/postgresql.svg?raw';
import sqliteSvg from '../../assets/db-logos/sqlite.svg?raw';
import mongodbSvg from '../../assets/db-logos/mongodb.svg?raw';
import elasticsearchSvg from '../../assets/db-logos/elasticsearch.svg?raw';
import redisSvg from '../../assets/db-logos/redis.svg?raw';
import mariadbSvg from '../../assets/db-logos/mariadb.svg?raw';
import clickhouseSvg from '../../assets/db-logos/clickhouse.svg?raw';

export interface DbBrand {
  /** 显示名,品牌官方 capitalization */
  name: string;
  /** 品牌主色(hex),用于背景 / icon currentColor */
  color: string;
  /** simple-icons SVG raw 字符串;null 表示没收录,走文字 chip fallback */
  svg: string | null;
}

/**
 * 用 dialect / sub_kind 关键字反查品牌信息。key 一律小写,匹配时也归一。
 * 顺序无关 — 用户后端 dialect 可能给 ``mysql`` / ``mysql8`` / ``maria`` 等变体,
 * resolveDbBrand 用 startsWith / includes 做模糊匹配。
 */
const BRANDS: Record<string, DbBrand> = {
  mysql:         { name: 'MySQL',         color: '#00758F', svg: mysqlSvg },
  mariadb:       { name: 'MariaDB',       color: '#003545', svg: mariadbSvg },
  postgres:      { name: 'PostgreSQL',    color: '#336791', svg: postgresqlSvg },
  postgresql:    { name: 'PostgreSQL',    color: '#336791', svg: postgresqlSvg },
  sqlite:        { name: 'SQLite',        color: '#003B57', svg: sqliteSvg },
  mongodb:       { name: 'MongoDB',       color: '#47A248', svg: mongodbSvg },
  mongo:         { name: 'MongoDB',       color: '#47A248', svg: mongodbSvg },
  elasticsearch: { name: 'Elasticsearch', color: '#005571', svg: elasticsearchSvg },
  es:            { name: 'Elasticsearch', color: '#005571', svg: elasticsearchSvg },
  redis:         { name: 'Redis',         color: '#DC382D', svg: redisSvg },
  clickhouse:    { name: 'ClickHouse',    color: '#FFCC01', svg: clickhouseSvg },
  // SQL Server / Oracle:simple-icons 没收,文字 chip + 品牌色
  mssql:         { name: 'SQL Server',    color: '#CC2927', svg: null },
  sqlserver:     { name: 'SQL Server',    color: '#CC2927', svg: null },
  oracle:        { name: 'Oracle',        color: '#F80000', svg: null },
  // 国产 DB(没官方 simple-icons),走文字 chip
  kingbase:      { name: '金仓数据库',     color: '#0E72C1', svg: null },
  dameng:        { name: '达梦数据库',     color: '#D4202C', svg: null },
};

/**
 * 把后端给的 dialect / sub_kind / display_name 等启发式归一到一个 DbBrand。
 *
 * 优先级:精确 dialect → 模糊关键字命中 → 通用 ``{name: 'Database', svg: null}``
 * 兜底(让 caller 至少能渲染文字 chip)。
 */
export function resolveDbBrand(hint: string | null | undefined): DbBrand {
  if (!hint) return { name: '数据库', color: '#6B7280', svg: null };
  const k = hint.toLowerCase();
  // 1. 精确匹配
  if (BRANDS[k]) return BRANDS[k];
  // 2. 模糊匹配(用户传 "mysql8" / "postgresql-pro" 之类)
  for (const [key, b] of Object.entries(BRANDS)) {
    if (k.includes(key)) return b;
  }
  return { name: '数据库', color: '#6B7280', svg: null };
}
