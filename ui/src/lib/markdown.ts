/**
 * Markdown + math renderer used by the Svelte chat view.
 *
 * Markdown: GFM subset (tables, task lists, fenced code, nested lists) via
 * `marked`. Output is sanitised with DOMPurify before handing to Svelte's
 * `{@html}` since model output is untrusted.
 *
 * Math: `$$…$$` (display) and `$…$` (inline) LaTeX expressions are extracted
 * to opaque placeholders before Markdown parsing so the `$` characters don't
 * interact with emphasis or other inline rules. After sanitisation the
 * placeholders are substituted with KaTeX-rendered HTML. A malformed
 * expression falls back to a literal code span rather than throwing.
 *
 * Streaming: partial input renders best-effort each tick. An unclosed fence
 * or half-written `$$` simply waits for the next chunk to complete.
 */

import { marked } from 'marked';
import DOMPurify from 'dompurify';
import katex from 'katex';

marked.setOptions({ gfm: true, breaks: false });

// Anchors opened via markdown should behave like external links.
DOMPurify.addHook('afterSanitizeAttributes', (node) => {
  if (node.nodeName === 'A') {
    node.setAttribute('target', '_blank');
    node.setAttribute('rel', 'noopener noreferrer');
  }
});

interface MathBlock {
  display: boolean;
  src: string;
}

const MATH_TOKEN_RE = /LOCAMATH-(\d+)-ENDLOCAMATH/g;

function extractMath(raw: string): { text: string; blocks: MathBlock[] } {
  const blocks: MathBlock[] = [];
  const mark = (display: boolean, src: string): string => {
    blocks.push({ display, src });
    return `LOCAMATH-${blocks.length - 1}-ENDLOCAMATH`;
  };
  let text = raw.replace(/\$\$([\s\S]+?)\$\$/g, (_, src: string) => mark(true, src));
  text = text.replace(/(^|[^\\$])\$([^\n$]+?)\$/g, (_, prev: string, src: string) => `${prev}${mark(false, src)}`);
  return { text, blocks };
}

function escHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function renderMath(src: string, display: boolean): string {
  try {
    return katex.renderToString(src.trim(), {
      displayMode: display,
      throwOnError: false,
      output: 'html',
    });
  } catch {
    const delim = display ? '$$' : '$';
    return `<code>${escHtml(delim + src + delim)}</code>`;
  }
}

export function renderMarkdown(raw: string): string {
  const { text, blocks } = extractMath(raw);
  const rawHtml = marked.parse(text, { async: false }) as string;
  const clean = DOMPurify.sanitize(rawHtml, {
    ADD_ATTR: ['target', 'rel'],
  });
  if (blocks.length === 0) return clean;
  return clean.replace(MATH_TOKEN_RE, (_, i: string) => {
    const block = blocks[Number(i)];
    return renderMath(block.src, block.display);
  });
}

/**
 * Splits assistant text into a reasoning trace (between `<think>…</think>`
 * tags, if any) and the final answer, matching the HTML UI's
 * splitThinkBlocks and SwiftUI's corresponding helper.
 */
export function splitThinkBlocks(text: string): { thinking: string; answer: string } {
  const thinkParts: string[] = [];
  let answer = '';
  const re = /<think>([\s\S]*?)(<\/think>|$)/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    answer += text.slice(last, m.index);
    thinkParts.push(m[1]);
    last = m.index + m[0].length;
    if (m[2] !== '</think>') break;
  }
  answer += text.slice(last);
  return { thinking: thinkParts.join('\n\n'), answer };
}
