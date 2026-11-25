"use client";

/**
 * Grafik sağ tık bağlam menüsü. Motor yalnız konum ve hedef bildirir; menü
 * React tarafında, siyah temada ve klavyeyle gezilebilir biçimde çizilir.
 *
 * Erişilebilirlik: `role="menu"`, öğeler `role="menuitem"`, ok tuşları etkin
 * öğeler arasında döner, Enter/Space çalıştırır, Esc kapatır.
 */

import { useEffect, useRef } from "react";
import type { ContextMenuAction, ContextMenuItem } from "./context-menu";
import { nextMenuIndex } from "./context-menu";

export interface GraphContextMenuProps {
  /** Tuval pikseli. */
  x: number;
  y: number;
  /** Kapsayıcı boyutu; menü taşmayacak biçimde kaydırılır. */
  containerWidth: number;
  containerHeight: number;
  items: readonly ContextMenuItem[];
  onAction: (action: ContextMenuAction) => void;
  onClose: () => void;
}

const MENU_WIDTH = 232;
const MENU_MARGIN = 8;

export function GraphContextMenu({
  x,
  y,
  containerWidth,
  containerHeight,
  items,
  onAction,
  onClose,
}: GraphContextMenuProps) {
  const menuRef = useRef<HTMLDivElement | null>(null);
  const itemRefs = useRef<Array<HTMLButtonElement | null>>([]);

  /* İlk etkin öğeye odaklan; menü klavyeyle hemen kullanılabilir. */
  useEffect(() => {
    const first = items.findIndex((item) => !item.disabled);
    if (first >= 0) itemRefs.current[first]?.focus();
  }, [items]);

  /* Dışarı tıklama ve Esc ile kapan. */
  useEffect(() => {
    const onPointerDown = (event: PointerEvent) => {
      if (!menuRef.current?.contains(event.target as Node)) onClose();
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
      }
    };
    window.addEventListener("pointerdown", onPointerDown, true);
    window.addEventListener("keydown", onKey);
    return () => {
      window.removeEventListener("pointerdown", onPointerDown, true);
      window.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const left = Math.max(
    MENU_MARGIN,
    Math.min(x, Math.max(MENU_MARGIN, containerWidth - MENU_WIDTH - MENU_MARGIN))
  );
  const estimatedHeight = items.length * 32 + 12;
  const top = Math.max(
    MENU_MARGIN,
    Math.min(y, Math.max(MENU_MARGIN, containerHeight - estimatedHeight - MENU_MARGIN))
  );

  const focusItem = (index: number | null) => {
    if (index === null) return;
    itemRefs.current[index]?.focus();
  };

  const handleKeyDown = (event: React.KeyboardEvent<HTMLButtonElement>, index: number) => {
    switch (event.key) {
      case "ArrowDown":
        event.preventDefault();
        focusItem(nextMenuIndex(index, 1, items));
        break;
      case "ArrowUp":
        event.preventDefault();
        focusItem(nextMenuIndex(index, -1, items));
        break;
      case "Home": {
        event.preventDefault();
        const first = items.findIndex((item) => !item.disabled);
        if (first >= 0) focusItem(first);
        break;
      }
      case "End": {
        event.preventDefault();
        for (let cursor = items.length - 1; cursor >= 0; cursor -= 1) {
          if (!items[cursor].disabled) {
            focusItem(cursor);
            break;
          }
        }
        break;
      }
      case "Tab":
        event.preventDefault();
        onClose();
        break;
      default:
        break;
    }
  };

  return (
    <div
      ref={menuRef}
      role="menu"
      aria-label="Düğüm eylemleri"
      className="absolute z-30 w-[232px] overflow-hidden rounded-[6px] border border-line-2 bg-overlay py-1 shadow-popover"
      style={{ left, top }}
      onContextMenu={(event) => event.preventDefault()}
    >
      {items.map((item, index) => (
        <button
          key={item.action}
          ref={(element) => {
            itemRefs.current[index] = element;
          }}
          type="button"
          role="menuitem"
          aria-disabled={item.disabled || undefined}
          disabled={item.disabled}
          onClick={() => {
            if (item.disabled) return;
            onAction(item.action);
            onClose();
          }}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              if (!item.disabled) {
                onAction(item.action);
                onClose();
              }
              return;
            }
            handleKeyDown(event, index);
          }}
          className={`flex w-full items-center justify-between gap-3 px-3 py-1.5 text-left text-[12px] transition-colors ${
            item.disabled
              ? "cursor-default text-ink-4"
              : item.danger
                ? "text-danger hover:bg-danger-soft"
                : "text-ink-2 hover:bg-hover hover:text-ink"
          } focus:outline-none focus-visible:bg-sunken focus-visible:text-ink`}
        >
          <span className="truncate">{item.label}</span>
          {item.hint && (
            <span className="shrink-0 text-[11px] text-ink-4">{item.hint}</span>
          )}
        </button>
      ))}
    </div>
  );
}

export default GraphContextMenu;
