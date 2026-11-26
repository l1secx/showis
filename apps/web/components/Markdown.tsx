"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { safeLinkHref } from "../lib/safe-url";

// Güvenli markdown: ham HTML işlenmez (react-markdown varsayılanı), `javascript:`
// bağlantıları temizlenir; dış bağlantılar yeni sekmede ve noopener ile açılır.
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md-body space-y-2 break-words text-[13px] leading-relaxed text-ink [&_code]:rounded [&_code]:bg-sunken [&_code]:px-1 [&_code]:text-[12px] [&_h1]:text-[15px] [&_h1]:font-semibold [&_h2]:text-[14px] [&_h2]:font-semibold [&_h3]:text-[13px] [&_h3]:font-semibold [&_li]:ml-4 [&_ol]:list-decimal [&_pre]:overflow-auto [&_pre]:rounded [&_pre]:bg-sunken [&_pre]:p-2 [&_table]:w-full [&_table]:text-[12px] [&_td]:border [&_td]:border-line [&_td]:px-2 [&_td]:py-1 [&_th]:border [&_th]:border-line [&_th]:px-2 [&_th]:py-1 [&_ul]:list-disc">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          table: ({ children }) => (
            <div className="scroll-x">
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children: label }) => {
            const safeHref = safeLinkHref(href);
            return safeHref ? (
              <a
                href={safeHref}
                target="_blank"
                rel="noreferrer noopener"
                className="text-accent-ink hover:underline"
              >
                {label}
              </a>
            ) : (
              <span>{label}</span>
            );
          },
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
