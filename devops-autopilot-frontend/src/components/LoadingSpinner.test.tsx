import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { LoadingSpinner } from './LoadingSpinner';

describe('LoadingSpinner', () => {
  it('defaults to full-screen, "lg"-sized presentation with the default message', () => {
    const { container } = render(<LoadingSpinner />);

    expect(screen.getByText('Loading…')).toBeInTheDocument();
    // fullScreen defaults to true -> min-h-screen wrapper.
    expect(container.firstElementChild).toHaveClass('min-h-screen');
    expect(container.firstElementChild).not.toHaveClass('py-8');
    // size defaults to "lg" -> 16x16 icon box.
    expect(container.querySelector('.w-16.h-16')).toBeInTheDocument();
  });

  it('renders inline (non-full-screen) when fullScreen is false', () => {
    const { container } = render(<LoadingSpinner fullScreen={false} />);

    expect(container.firstElementChild).toHaveClass('py-8');
    expect(container.firstElementChild).not.toHaveClass('min-h-screen');
  });

  it('applies the "sm" size styles when size="sm"', () => {
    const { container } = render(<LoadingSpinner size="sm" />);

    expect(container.querySelector('.w-8.h-8')).toBeInTheDocument();
    expect(container.querySelector('.w-16.h-16')).not.toBeInTheDocument();
  });

  it('applies the "md" size styles when size="md"', () => {
    const { container } = render(<LoadingSpinner size="md" />);

    expect(container.querySelector('.w-12.h-12')).toBeInTheDocument();
  });

  it('renders a custom message when provided', () => {
    render(<LoadingSpinner message="Deploying…" />);

    expect(screen.getByText('Deploying…')).toBeInTheDocument();
  });
});
