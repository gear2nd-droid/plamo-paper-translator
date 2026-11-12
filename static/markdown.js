/*
 * Small, safe Markdown renderer for paper notes.
 *
 * Supported: headings (#), paragraphs (single newlines kept), lists (-, *, +,
 * 1.) with nesting by indentation, block quotes (>), fenced code (```),
 * horizontal rules, **bold**, *italic*, `code`, [text](https://...), inline
 * math $...$ and display math $$...$$ (KaTeX when loaded), and paper
 * references [[図2]] / ![[図2]] resolved by the caller.
 *
 * All text is escaped before any markup is added; no raw HTML is accepted.
 */
(function (root) {
  const ESCAPES = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'};
  const escapeText = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => ESCAPES[character]);

  const LIST_ITEM = /^(\s*)([-*+]|\d{1,3}[.)])\s+(.*)$/;
  const RULE = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const HEADING = /^(#{1,6})\s+(.*?)\s*#*\s*$/;

  function renderMath(tex, display) {
    const katex = root.katex;
    if (katex) {
      try {
        return katex.renderToString(tex, {
          displayMode: display, throwOnError: false, trust: false, strict: 'ignore', maxExpand: 1000, maxSize: 20,
        });
      } catch (_) { /* fall through to the source text */ }
    }
    return `<code class="math-fallback">${escapeText(display ? `$$${tex}$$` : `$${tex}$`)}</code>`;
  }

  function defaultOptions(options) {
    return {
      text: options?.text || escapeText,
      reference: options?.reference || ((label, embed) => escapeText(`${embed ? '!' : ''}[[${label}]]`)),
    };
  }

  function renderInline(value, options) {
    const tokens = [];
    const keep = (html) => `${tokens.push(html) - 1}`;
    let text = String(value ?? '');
    text = text.replace(/`([^`\n]+)`/g, (_, code) => keep(`<code>${escapeText(code)}</code>`));
    // $...$ needs non-space just inside both dollars, so "$5 and $10" stays text.
    text = text.replace(/\$(?=\S)((?:\\.|[^$\\\n])+?)(?<=\S)\$(?!\d)/g, (_, tex) => keep(renderMath(tex, false)));
    text = text.replace(/(!?)\[\[([^\[\]\n]{1,60})\]\]/g, (_, bang, label) => keep(options.reference(label.trim(), Boolean(bang))));
    text = text.replace(/\[([^\[\]\n]+)\]\((https?:\/\/[^\s()<>]+)\)/g, (_, label, href) => keep(
      `<a href="${escapeText(href)}" target="_blank" rel="noreferrer">${escapeText(label)}</a>`,
    ));
    let html = text
      .split(/(\d+)/)
      .map((piece) => (/^\d+$/.test(piece) ? piece : options.text(piece)))
      .join('');
    html = html
      .replace(/\*\*(?=\S)([^*]+?)(?<=\S)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*\w])\*(?=\S)([^*]+?)(?<=\S)\*(?![*\w])/g, '$1<em>$2</em>');
    return html.replace(/(\d+)/g, (_, index) => tokens[Number(index)]);
  }

  function indentOf(line) {
    return line.match(/^\s*/)[0].replace(/\t/g, '  ').length;
  }

  function startsBlock(line) {
    return /^\s*(?:#{1,6}\s|>|```|\$\$)/.test(line) || LIST_ITEM.test(line) || RULE.test(line);
  }

  function renderList(lines, start, options) {
    const first = lines[start].match(LIST_ITEM);
    const baseIndent = indentOf(first[1]);
    const ordered = /\d/.test(first[2]);
    const items = [];
    let index = start;
    while (index < lines.length) {
      const line = lines[index];
      const match = line.match(LIST_ITEM);
      if (match && indentOf(match[1]) === baseIndent && /\d/.test(match[2]) === ordered) {
        items.push({text: match[3], children: []});
        index += 1;
        continue;
      }
      if (line.trim() && items.length && indentOf(line) > baseIndent) {
        items[items.length - 1].children.push(line.slice(Math.min(indentOf(line), baseIndent + 2)));
        index += 1;
        continue;
      }
      if (!line.trim() && index + 1 < lines.length && items.length && indentOf(lines[index + 1]) > baseIndent && lines[index + 1].trim()) {
        index += 1;
        continue;
      }
      break;
    }
    const tag = ordered ? 'ol' : 'ul';
    const startNumber = ordered ? Number(first[2].replace(/\D/g, '')) : 1;
    const startAttribute = ordered && startNumber !== 1 ? ` start="${startNumber}"` : '';
    const body = items.map((item) => {
      const nested = item.children.length ? renderBlocks(item.children, options) : '';
      return `<li>${renderInline(item.text, options)}${nested}</li>`;
    }).join('');
    return {html: `<${tag}${startAttribute}>${body}</${tag}>`, next: index};
  }

  function renderBlocks(lines, options) {
    const output = [];
    let index = 0;
    while (index < lines.length) {
      const line = lines[index];
      const trimmed = line.trim();
      if (!trimmed) { index += 1; continue; }
      if (trimmed.startsWith('```')) {
        const code = [];
        index += 1;
        while (index < lines.length && !lines[index].trim().startsWith('```')) { code.push(lines[index]); index += 1; }
        index += 1;
        output.push(`<pre><code>${escapeText(code.join('\n'))}</code></pre>`);
        continue;
      }
      if (trimmed.startsWith('$$')) {
        const single = trimmed.match(/^\$\$([\s\S]+)\$\$$/);
        if (single) {
          output.push(`<div class="math-display">${renderMath(single[1].trim(), true)}</div>`);
          index += 1;
          continue;
        }
        const tex = [trimmed.slice(2)];
        index += 1;
        while (index < lines.length && !lines[index].trim().endsWith('$$')) { tex.push(lines[index]); index += 1; }
        if (index < lines.length) { tex.push(lines[index].trim().slice(0, -2)); index += 1; }
        output.push(`<div class="math-display">${renderMath(tex.join('\n').trim(), true)}</div>`);
        continue;
      }
      const heading = line.match(HEADING);
      if (heading) {
        const level = heading[1].length;
        output.push(`<h${Math.min(6, level + 2)} class="md-heading md-h${level}">${renderInline(heading[2], options)}</h${Math.min(6, level + 2)}>`);
        index += 1;
        continue;
      }
      if (RULE.test(line)) { output.push('<hr>'); index += 1; continue; }
      if (trimmed.startsWith('>')) {
        const quoted = [];
        while (index < lines.length && lines[index].trim().startsWith('>')) {
          quoted.push(lines[index].trim().replace(/^>\s?/, ''));
          index += 1;
        }
        output.push(`<blockquote>${renderBlocks(quoted, options)}</blockquote>`);
        continue;
      }
      if (LIST_ITEM.test(line)) {
        const list = renderList(lines, index, options);
        output.push(list.html);
        index = list.next;
        continue;
      }
      const paragraph = [line.trim()];
      index += 1;
      while (index < lines.length && lines[index].trim() && !startsBlock(lines[index])) {
        paragraph.push(lines[index].trim());
        index += 1;
      }
      output.push(`<p>${paragraph.map((part) => renderInline(part, options)).join('<br>')}</p>`);
    }
    return output.join('');
  }

  function renderMarkdown(source, options) {
    const lines = String(source ?? '').replace(/\r\n?/g, '\n').split('\n');
    return renderBlocks(lines, defaultOptions(options));
  }

  /** Plain text for one-line previews such as library cards. */
  function markdownToPlainText(source) {
    return String(source ?? '')
      .replace(/\r\n?/g, '\n')
      .replace(/^```.*$/gm, '')
      .replace(/^\s*#{1,6}\s+/gm, '')
      .replace(/^\s*>\s?/gm, '')
      .replace(/^\s*(?:[-*+]|\d{1,3}[.)])\s+/gm, '')
      .replace(/^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/gm, '')
      .replace(/!?\[\[([^\]]+)\]\]/g, '$1')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)]+)\)/g, '$1')
      .replace(/\$\$?([^$]+)\$\$?/g, '$1')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .replace(/[ \t]+\n/g, '\n')
      .replace(/\n{3,}/g, '\n\n')
      .trim();
  }

  const api = {renderMarkdown, renderInline: (value, options) => renderInline(value, defaultOptions(options)), markdownToPlainText, escapeText};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PaperMarkdown = api;
})(typeof window !== 'undefined' ? window : globalThis);
