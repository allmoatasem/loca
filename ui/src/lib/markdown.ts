/**
 * Minimal, dependency-free Markdown renderer used by the Svelte chat view.
 *
 * Scope: paragraphs, headers (#/##/###), bold (**x**), italic (*x*),
 * inline code (`x`), fenced code blocks (```lang...```), bullet and
 * numbered lists, blockquotes, and links ([label](url)).
 *
 * Not supported (yet): tables, task lists, footnotes, Prism highlighting.
 * Those land in Phase 4b alongside the attachment UI.
 *
 * Returns HTML as a string. Callers render with `{@html …}`. Input is
 * escaped before any markup wrapping, so user- or model-emitted content
 * cannot inject raw HTML.
 */

function escHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function inline(line: string): string {
  let s = escHtml(line);
  // Bold before italic so ** isn't swallowed by * matchers
  s = s.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g,
    '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
  return s;
}

export function renderMarkdown(raw: string): string {
  // Pull out complete fenced blocks first so their contents don't get
  // inline-processed. Unclosed fences (streaming mid-code-block) render
  // as a "loading" code block so the UX stays smooth.
  const fences: string[] = [];
  let text = raw.replace(/```([^\n]*)\n?([\s\S]*?)```/g, (_, lang: string, code: string) => {
    const cls = lang.trim() ? ` class="lang-${escHtml(lang.trim())}"` : '';
    const html = `<pre><code${cls}>${escHtml(code)}</code></pre>`;
    fences.push(html);
    return `\u0000FENCE${fences.length - 1}\u0000`;
  });
  // Handle a trailing unclosed fence for live streaming.
  const openIdx = text.lastIndexOf('```');
  if (openIdx >= 0) {
    const after = text.slice(openIdx + 3);
    const newline = after.indexOf('\n');
    const lang = newline >= 0 ? after.slice(0, newline).trim() : after.trim();
    const code = newline >= 0 ? after.slice(newline + 1) : '';
    const cls = lang ? ` class="lang-${escHtml(lang)}"` : '';
    const html = `<pre><code${cls}>${escHtml(code)}▌</code></pre>`;
    fences.push(html);
    text = text.slice(0, openIdx) + `\u0000FENCE${fences.length - 1}\u0000`;
  }

  const out: string[] = [];
  const lines = text.split(/\r?\n/);
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    // Sentinel for a parked fenced block
    const fenceMatch = line.match(/^\u0000FENCE(\d+)\u0000$/);
    if (fenceMatch) {
      out.push(fences[parseInt(fenceMatch[1], 10)]);
      i++;
      continue;
    }
    // Headers
    const h = line.match(/^(#{1,3})\s+(.+)$/);
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(h[2])}</h${lvl}>`);
      i++;
      continue;
    }
    // Blockquote
    const bq = line.match(/^>\s?(.*)$/);
    if (bq) {
      out.push(`<blockquote>${inline(bq[1])}</blockquote>`);
      i++;
      continue;
    }
    // Bullet list
    if (/^\s*[-*]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*[-*]\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ul>${items.join('')}</ul>`);
      continue;
    }
    // Numbered list
    if (/^\s*\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+\.\s+/.test(lines[i])) {
        items.push(`<li>${inline(lines[i].replace(/^\s*\d+\.\s+/, ''))}</li>`);
        i++;
      }
      out.push(`<ol>${items.join('')}</ol>`);
      continue;
    }
    // Blank → paragraph separator
    if (line.trim() === '') { i++; continue; }
    // Paragraph: gather consecutive non-empty non-special lines
    const paraLines: string[] = [];
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,3}\s|>\s?|\s*[-*]\s|\s*\d+\.\s|\u0000FENCE)/.test(lines[i])
    ) {
      paraLines.push(lines[i]);
      i++;
    }
    if (paraLines.length) {
      out.push(`<p>${inline(paraLines.join(' '))}</p>`);
    }
  }
  return out.join('');
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
