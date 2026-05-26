import { type ClassValue, clsx } from 'clsx';
import { twMerge } from 'tailwind-merge';

/** Tailwind v4 友好的 class 合并工具：去重 + 后者覆盖前者 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
