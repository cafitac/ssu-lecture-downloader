import { useEffect, useRef } from 'react';

interface Props {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  label: string;
  onChange: (checked: boolean) => void;
}

/** Native checkbox with tri-state support. The visible label is supplied by the surrounding row. */
export function Checkbox({ checked, indeterminate = false, disabled, label, onChange }: Props) {
  const ref = useRef<HTMLInputElement>(null);
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [checked, indeterminate]);
  return (
    <input
      ref={ref}
      className="checkbox"
      type="checkbox"
      aria-label={label}
      checked={checked}
      disabled={disabled}
      onChange={(event) => onChange(event.currentTarget.checked)}
    />
  );
}
