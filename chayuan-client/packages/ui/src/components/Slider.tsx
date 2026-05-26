import * as React from 'react';
import * as SliderPrimitive from '@radix-ui/react-slider';
import { cn } from '../lib/cn';

/**
 * Slider —— 字号、AI 设备调优、其他范围参数。
 *
 * 设计稿:细 track,brand 蓝填充,白圆 thumb 带描边。
 */
export const Slider = React.forwardRef<
  React.ElementRef<typeof SliderPrimitive.Root>,
  React.ComponentPropsWithoutRef<typeof SliderPrimitive.Root>
>(({ className, ...props }, ref) => (
  <SliderPrimitive.Root
    ref={ref}
    className={cn('relative flex w-full touch-none select-none items-center', className)}
    {...props}
  >
    <SliderPrimitive.Track className="relative h-1 w-full grow overflow-hidden rounded-full bg-[var(--cy-border-default)]">
      <SliderPrimitive.Range className="absolute h-full bg-[var(--cy-brand-500)]" />
    </SliderPrimitive.Track>
    <SliderPrimitive.Thumb
      className={cn(
        'block h-4 w-4 rounded-full border-2 border-[var(--cy-brand-500)] bg-white shadow',
        'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--cy-brand-500)] focus-visible:ring-offset-2',
        'disabled:pointer-events-none disabled:opacity-50',
      )}
    />
  </SliderPrimitive.Root>
));
Slider.displayName = 'Slider';
