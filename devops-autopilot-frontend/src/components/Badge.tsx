import React from 'react';

export type BadgeVariant = 'success' | 'warning' | 'error' | 'info' | 'default' | 'purple';

interface BadgeProps {
  children: React.ReactNode;
  variant?: BadgeVariant;
  size?: 'sm' | 'md';
}

export const Badge: React.FC<BadgeProps> = ({ children, variant = 'default', size = 'sm' }) => {
  const variants = {
    success: 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 shadow-[0_0_8px_rgba(16,185,129,0.15)]',
    warning: 'bg-amber-500/15 text-amber-400  border border-amber-500/30  shadow-[0_0_8px_rgba(245,158,11,0.15)]',
    error: 'bg-rose-500/15   text-rose-400   border border-rose-500/30   shadow-[0_0_8px_rgba(244,63,94,0.15)]',
    info: 'bg-cyan-500/15   text-cyan-400   border border-cyan-500/30   shadow-[0_0_8px_rgba(34,211,238,0.15)]',
    purple: 'bg-purple-500/15 text-purple-400 border border-purple-500/30 shadow-[0_0_8px_rgba(168,85,247,0.15)]',
    default: 'bg-white/5       text-gray-300   border border-white/10',
  };

  const sizes = {
    sm: 'px-2.5 py-0.5 text-xs gap-1',
    md: 'px-3    py-1   text-sm gap-1.5',
  };

  return (
    <span
      className={`inline-flex items-center rounded-full font-medium tracking-wide ${variants[variant]} ${sizes[size]}`}
    >
      {children}
    </span>
  );
};