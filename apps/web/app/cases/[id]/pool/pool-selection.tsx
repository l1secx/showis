"use client";

import { useEffect, useRef } from "react";

/**
 * Üç durumlu seçim kutusu: hiçbiri / bir kısmı / tümü. Yerel `input`
 * kutusu `indeterminate` özelliğini yalnız DOM üzerinden kabul eder;
 * bu yüzden ref ile senkronlanır.
 */
export function TriStateCheckbox({
  checked,
  indeterminate = false,
  disabled = false,
  label,
  title,
  onChange,
}: {
  checked: boolean;
  indeterminate?: boolean;
  disabled?: boolean;
  label: string;
  title?: string;
  onChange: () => void;
}) {
  const ref = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (ref.current) ref.current.indeterminate = indeterminate && !checked;
  }, [indeterminate, checked]);

  return (
    <input
      ref={ref}
      type="checkbox"
      checked={checked}
      disabled={disabled}
      onChange={onChange}
      aria-label={label}
      title={title}
      className="h-3.5 w-3.5 shrink-0 cursor-pointer rounded-[3px] accent-[rgb(var(--accent))] disabled:cursor-not-allowed disabled:opacity-40"
    />
  );
}
