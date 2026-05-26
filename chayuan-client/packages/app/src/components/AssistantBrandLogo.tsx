import * as React from 'react';
import { cn } from '@chayuan/ui';
import { CHAYUAN_LOGO_URL } from '../lib/brandAssets';

const ASSISTANT_LOGO_FOLD_STYLE = `
@keyframes cy-assistant-logo-fold-left {
  0%, 100% { transform: rotateY(0deg) translateZ(0); filter: brightness(1) drop-shadow(0 1px 1px rgba(15, 23, 42, 0.10)); }
  45% { transform: rotateY(58deg) translateZ(2px); filter: brightness(1.08) drop-shadow(0 2px 2px rgba(15, 23, 42, 0.12)); }
  65% { transform: rotateY(28deg) translateZ(1px); filter: brightness(1.04) drop-shadow(0 1px 1px rgba(15, 23, 42, 0.10)); }
}
@keyframes cy-assistant-logo-fold-right {
  0%, 100% { transform: rotateY(0deg) translateZ(0); filter: brightness(1) drop-shadow(0 1px 1px rgba(15, 23, 42, 0.10)); }
  45% { transform: rotateY(-58deg) translateZ(2px); filter: brightness(0.98) drop-shadow(0 2px 2px rgba(15, 23, 42, 0.12)); }
  65% { transform: rotateY(-28deg) translateZ(1px); filter: brightness(1.02) drop-shadow(0 1px 1px rgba(15, 23, 42, 0.10)); }
}
.cy-assistant-logo-fold {
  perspective: 320px;
  transform-style: preserve-3d;
  background: transparent;
}
.cy-assistant-logo-fold-half {
  background-image: var(--cy-assistant-logo-image);
  background-repeat: no-repeat;
  background-size: 200% 100%;
  background-color: transparent;
  backface-visibility: hidden;
  transform-style: preserve-3d;
  animation-duration: 1.05s;
  animation-timing-function: cubic-bezier(0.45, 0, 0.2, 1);
  animation-iteration-count: infinite;
}
.cy-assistant-logo-fold-half-left {
  background-position: left center;
  transform-origin: right center;
  animation-name: cy-assistant-logo-fold-left;
}
.cy-assistant-logo-fold-half-right {
  background-position: right center;
  transform-origin: left center;
  animation-name: cy-assistant-logo-fold-right;
}
`;

let styleInjected = false;

function ensureAssistantLogoStyle(): void {
  if (typeof document === 'undefined' || styleInjected) return;
  styleInjected = true;
  const style = document.createElement('style');
  style.dataset.cyAssistantLogo = 'fold';
  style.textContent = ASSISTANT_LOGO_FOLD_STYLE;
  document.head.appendChild(style);
}

export const AssistantBrandLogo: React.FC<{ running?: boolean; className?: string }> = ({
  running = false,
  className = 'h-5 w-5',
}) => {
  React.useEffect(() => {
    if (running) ensureAssistantLogoStyle();
  }, [running]);

  if (!running) {
    return (
      <img
        src={CHAYUAN_LOGO_URL}
        alt="察元"
        className={cn('shrink-0 bg-transparent object-contain', className)}
      />
    );
  }

  return (
    <span
      className={cn('cy-assistant-logo-fold relative inline-block shrink-0 overflow-visible bg-transparent', className)}
      style={{ '--cy-assistant-logo-image': `url("${CHAYUAN_LOGO_URL}")` } as React.CSSProperties}
      role="img"
      aria-label="察元正在执行"
    >
      <span className="cy-assistant-logo-fold-half cy-assistant-logo-fold-half-left absolute bottom-0 left-0 top-0 w-1/2 overflow-hidden rounded-l-[inherit]" />
      <span className="cy-assistant-logo-fold-half cy-assistant-logo-fold-half-right absolute bottom-0 right-0 top-0 w-1/2 overflow-hidden rounded-r-[inherit]" />
      <span className="absolute left-1/2 top-0 h-full w-px -translate-x-1/2 bg-cyan-100/40 opacity-60" />
    </span>
  );
};
