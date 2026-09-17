import React from 'react';
import LogoMark from './LogoMark';

type LoadingSpinnerSize = 'sm' | 'md' | 'lg';

interface LoadingSpinnerProps {
  message?: string;
  /**
   * Whether the spinner should fill the viewport (min-h-screen). Defaults to
   * `true` to preserve existing behavior for call sites that render this as
   * a full-page loading state. Pass `false` to render an inline/contained
   * spinner instead, e.g. inside a modal tab or a smaller panel.
   */
  fullScreen?: boolean;
  /**
   * Visual size of the spinner icon/text. Defaults to `lg` (the original
   * size) for backward compatibility. Use `sm` or `md` for inline usage
   * inside smaller containers.
   */
  size?: LoadingSpinnerSize;
}

const SIZE_STYLES: Record<LoadingSpinnerSize, { box: string; logo: number; text: string; margin: string }> = {
  sm: { box: 'w-8 h-8', logo: 16, text: 'text-xs', margin: 'mb-3' },
  md: { box: 'w-12 h-12', logo: 24, text: 'text-sm', margin: 'mb-4' },
  lg: { box: 'w-16 h-16', logo: 32, text: 'text-sm', margin: 'mb-6' },
};

export const LoadingSpinner: React.FC<LoadingSpinnerProps> = ({
  message = 'Loading…',
  fullScreen = true,
  size = 'lg',
}) => {
  const { box, logo, text, margin } = SIZE_STYLES[size];

  return (
    <div
      className={`flex items-center justify-center ${fullScreen ? 'min-h-screen' : 'py-8'}`}
      style={{ background: 'var(--bg-base)' }}
    >
      <div className="text-center animate-fade-in">
        <div className={`relative inline-flex ${margin}`}>
          {/* Outer pulse ring */}
          <div
            className="absolute inset-0 rounded-2xl animate-ping"
            style={{ background: 'rgba(34,211,238,0.15)', animationDuration: '1.5s' }}
          />
          <div
            className={`${box} rounded-2xl flex items-center justify-center`}
            style={{
              background: 'linear-gradient(145deg, #0d1117, #161b22)',
              border: '1px solid rgba(34,211,238,0.25)',
              boxShadow: '0 0 32px rgba(34,211,238,0.2)',
            }}
          >
            <LogoMark size={logo} />
          </div>
        </div>
        <p className={`${text} text-gray-500 font-medium`}>{message}</p>
      </div>
    </div>
  );
};