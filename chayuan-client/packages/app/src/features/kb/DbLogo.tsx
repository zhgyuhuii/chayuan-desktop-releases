/**
 * 数据库官方 logo 渲染器。
 *
 * 接收 dialect hint(后端 dialect / sub_kind / 描述里关键字均可),用
 * ``resolveDbBrand`` 模糊匹配到 DbBrand,然后:
 *  - simple-icons 收录的(MySQL/PG/SQLite/MongoDB/ES/Redis/MariaDB/CH)→
 *    inline SVG 注入 ``fill="currentColor"``,CSS color 用品牌 hex 上色
 *  - 没收录的(SQL Server/Oracle/金仓/达梦)→ 文字 chip 用品牌色背景
 */
import * as React from 'react';
import { Database } from 'lucide-react';
import { cn } from '@chayuan/ui';
import { resolveDbBrand } from './dbLogos';

export interface DbLogoProps {
  /** 后端 dialect / sub_kind / KB 描述里的关键字 — 任何能识别 DB 类型的字串 */
  dialect?: string | null;
  /** logo 尺寸(px),默认 28 */
  size?: number;
  /** 是否显示文字名字(SVG 旁边) */
  showName?: boolean;
  className?: string;
}

export const DbLogo: React.FC<DbLogoProps> = ({
  dialect, size = 28, showName = false, className,
}) => {
  const brand = resolveDbBrand(dialect);

  if (brand.svg) {
    // 注入 fill="currentColor" 让 CSS color 控品牌色;
    // simple-icons 的 SVG root 没指定 fill,自然 inherit black,我们覆盖。
    const svgWithFill = brand.svg.replace('<svg ', '<svg fill="currentColor" ');
    return (
      <span className={cn('inline-flex items-center gap-1.5', className)}>
        <span
          style={{ color: brand.color, width: size, height: size }}
          className="inline-flex items-center justify-center"
          // 必须 dangerouslySetInnerHTML — simple-icons SVG 信任源(CC0,
          // 构建期 import 进来,没有用户输入污染风险)
          dangerouslySetInnerHTML={{ __html: svgWithFill }}
          aria-label={brand.name}
        />
        {showName && (
          <span className="text-xs font-medium" style={{ color: brand.color }}>
            {brand.name}
          </span>
        )}
      </span>
    );
  }

  // Fallback:simple-icons 没收的 DB(SQL Server / Oracle / 金仓 / 达梦 …)
  // 用品牌色 chip + Database icon
  return (
    <span
      className={cn(
        'inline-flex items-center gap-1.5 rounded-md px-2 py-0.5 text-xs font-medium',
        className,
      )}
      style={{ backgroundColor: `${brand.color}1a`, color: brand.color }}
      aria-label={brand.name}
    >
      <Database style={{ width: size * 0.5, height: size * 0.5 }} />
      {showName && <span>{brand.name}</span>}
    </span>
  );
};

/** 仅返回品牌信息,不渲染 — caller 自定义布局时用。 */
export { resolveDbBrand } from './dbLogos';
