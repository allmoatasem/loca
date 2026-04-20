/**
 * Markdown + math + syntax highlighting renderer used by the Svelte chat
 * view.
 *
 * Markdown: GFM subset (tables, task lists, fenced code, nested lists) via
 * `marked`. Output is sanitised with DOMPurify before handing to Svelte's
 * `{@html}` since model output is untrusted.
 *
 * Syntax highlighting: Prism is bundled via marked-highlight, so the
 * `<pre><code>` output already contains `<span class="token …">` wrappers
 * by the time the DOM sees it. No post-mount highlighter pass needed, and
 * no external script-load timing race.
 *
 * Math: `$$…$$` (display) and `$…$` (inline) LaTeX expressions are
 * extracted to opaque placeholders before Markdown parsing so the `$`
 * characters don't interact with emphasis or other inline rules. After
 * sanitisation the placeholders are substituted with KaTeX-rendered HTML.
 * Malformed expressions fall back to a literal code span rather than
 * throwing.
 */

import { marked } from 'marked';
import { markedHighlight } from 'marked-highlight';
import DOMPurify from 'dompurify';
import katex from 'katex';
import Prism from 'prismjs';
// Core Prism theme for the token colours.
import 'prismjs/themes/prism.css';
// Language grammars bundled with the app. Keep in sync with what Loca
// typically emits — rarer languages fall back to unhighlighted <code>.
import 'prismjs/components/prism-markup';
import 'prismjs/components/prism-markup-templating';
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-typescript';
import 'prismjs/components/prism-python';
import 'prismjs/components/prism-bash';
import 'prismjs/components/prism-json';
import 'prismjs/components/prism-yaml';
import 'prismjs/components/prism-css';
import 'prismjs/components/prism-swift';
import 'prismjs/components/prism-rust';
import 'prismjs/components/prism-go';
import 'prismjs/components/prism-java';
import 'prismjs/components/prism-c';
import 'prismjs/components/prism-cpp';
import 'prismjs/components/prism-sql';
import 'prismjs/components/prism-diff';

marked.use(
  markedHighlight({
    langPrefix: 'language-',
    highlight(code, lang) {
      const grammar = lang && Prism.languages[lang];
      if (grammar) return Prism.highlight(code, grammar, lang);
      return code;
    },
  }),
);
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
 * Replace raw tool-call JSON / `<tool_call>` tags with a discreet
 * `> 🔧 called <tool>` blockquote. Some models (and most agentic
 * clients) leak the structured payload back into the assistant
 * bubble — without filtering, the user sees a wall of JSON that
 * means nothing to them. We never strip arguments without a marker:
 * if we can't extract the tool name, we leave the original text
 * alone so a curious user can still inspect it.
 */
/**
 * Wrap `[memory: N]` citations in a clickable anchor. The href encodes
 * the memory index so the chat container can intercept the click and
 * route to the Memory panel highlighted on that row. Index-only links
 * are good enough for v1: per-turn provenance plumbing (id ↔ index)
 * is the next step.
 */
export function linkMemoryCitations(raw: string, citationIds: string[] = []): string {
  if (!raw) return raw;
  // Encoded as a same-origin hash fragment so DOMPurify keeps the
  // anchor intact — custom URL schemes are stripped by default.
  // Chat container intercepts clicks on `a[href^="#loca-memory-"]`.
  // When a per-turn citation map is available we encode the real
  // memory id in the fragment so the click can deep-link directly
  // to that row. Otherwise falls back to the index — the panel
  // opens but can't highlight a specific memory.
  return raw.replace(
    /\[memory:\s*(\d+)\]/g,
    (_match, idx: string) => {
      const id = citationIds[Number(idx) - 1];
      const target = id ? `id:${encodeURIComponent(id)}` : `idx:${idx}`;
      return `[memory:${idx}](#loca-memory-${target})`;
    },
  );
}

export function stripToolCallJson(raw: string): string {
  if (!raw) return raw;
  let text = raw;
  // <tool_call>{...}</tool_call> wrappers (Qwen / many agentic clients)
  text = text.replace(/<tool_call>([\s\S]*?)<\/tool_call>/gi, (_match, inner: string) => {
    const name = extractToolName(inner);
    return name ? `\n\n> 🔧 called \`${name}\`\n\n` : '\n\n> 🔧 (tool call)\n\n';
  });
  // ```json fences whose body is a tool-call object
  text = text.replace(/```json\s*([\s\S]*?)```/gi, (match, inner: string) => {
    const name = extractToolName(inner);
    return name ? `\n\n> 🔧 called \`${name}\`\n\n` : match;
  });
  // Bare `{"name": "...", "arguments": {...}}` JSON blocks at the start
  // of a paragraph (most common shape from llama-style emit).
  text = text.replace(
    /(^|\n)\s*(\{[\s\S]*?"(?:name|function|tool_name)"\s*:\s*"([^"]+)"[\s\S]*?\})\s*(?=\n|$)/g,
    (_match, lead: string, _block: string, name: string) => {
      return `${lead}\n> 🔧 called \`${name}\`\n`;
    },
  );
  return text;
}

function extractToolName(jsonish: string): string | null {
  const m = jsonish.match(/"(?:name|function|tool_name)"\s*:\s*"([^"]+)"/);
  return m ? m[1] : null;
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
