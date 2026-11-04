/**
 * 轻量 Markdown → HTML（对话 / 报告共用）
 * 支持：标题、表格、列表、引用、代码块、粗体/斜体/行内代码、分隔线
 */
export function renderMarkdown(md: string): string {
  if (!md) return '';

  const escaped = md
    .replace(/\r\n/g, '\n')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;');

  const fences: string[] = [];
  let text = escaped.replace(/```([\w-]*)\n?([\s\S]*?)```/g, (_m, lang: string, code: string) => {
    const i = fences.length;
    fences.push(
      `<pre class="md-pre"><code class="language-${lang || 'text'}">${code.replace(/\n$/, '')}</code></pre>`
    );
    return `\n%%FENCE_${i}%%\n`;
  });

  const lines = text.split('\n');
  const out: string[] = [];
  let i = 0;

  const inline = (s: string) =>
    s
      .replace(/`([^`]+)`/g, '<code class="md-code">$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong class="md-strong">$1</strong>')
      .replace(/\*([^*\n]+)\*/g, '<em class="md-em">$1</em>');

  const isSepRow = (line: string) =>
    /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line.trim());

  const splitCells = (line: string) => {
    let row = line.trim();
    if (row.startsWith('|')) row = row.slice(1);
    if (row.endsWith('|')) row = row.slice(0, -1);
    return row.split('|').map((c) => c.trim());
  };

  while (i < lines.length) {
    const line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) {
      i += 1;
      continue;
    }

    const fenceMatch = trimmed.match(/^%%FENCE_(\d+)%%$/);
    if (fenceMatch) {
      out.push(fences[Number(fenceMatch[1])] || '');
      i += 1;
      continue;
    }

    if (/^---+$/.test(trimmed) || /^\*\*\*+$/.test(trimmed)) {
      out.push('<hr class="md-hr" />');
      i += 1;
      continue;
    }

    // 表格：连续 | 行
    if (trimmed.includes('|') && trimmed.startsWith('|')) {
      const tableLines: string[] = [];
      while (i < lines.length && lines[i].trim().includes('|') && lines[i].trim().startsWith('|')) {
        if (!isSepRow(lines[i])) tableLines.push(lines[i]);
        i += 1;
      }
      if (tableLines.length) {
        const [header, ...body] = tableLines;
        const ths = splitCells(header)
          .map((c) => `<th class="md-th">${inline(c)}</th>`)
          .join('');
        const trs = body
          .map((row) => {
            const tds = splitCells(row)
              .map((c) => `<td class="md-td">${inline(c)}</td>`)
              .join('');
            return `<tr class="md-tr">${tds}</tr>`;
          })
          .join('');
        out.push(
          `<div class="md-table-wrap"><table class="md-table"><thead><tr class="md-tr">${ths}</tr></thead><tbody>${trs}</tbody></table></div>`
        );
      }
      continue;
    }

    if (/^######\s+/.test(trimmed)) {
      out.push(`<h6 class="md-h6">${inline(trimmed.replace(/^######\s+/, ''))}</h6>`);
      i += 1;
      continue;
    }
    if (/^#####\s+/.test(trimmed)) {
      out.push(`<h5 class="md-h5">${inline(trimmed.replace(/^#####\s+/, ''))}</h5>`);
      i += 1;
      continue;
    }
    if (/^####\s+/.test(trimmed)) {
      out.push(`<h4 class="md-h4">${inline(trimmed.replace(/^####\s+/, ''))}</h4>`);
      i += 1;
      continue;
    }
    if (/^###\s+/.test(trimmed)) {
      out.push(`<h3 class="md-h3">${inline(trimmed.replace(/^###\s+/, ''))}</h3>`);
      i += 1;
      continue;
    }
    if (/^##\s+/.test(trimmed)) {
      out.push(`<h2 class="md-h2">${inline(trimmed.replace(/^##\s+/, ''))}</h2>`);
      i += 1;
      continue;
    }
    if (/^#\s+/.test(trimmed)) {
      out.push(`<h1 class="md-h1">${inline(trimmed.replace(/^#\s+/, ''))}</h1>`);
      i += 1;
      continue;
    }

    if (/^>\s?/.test(trimmed)) {
      const quote: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i].trim())) {
        quote.push(lines[i].trim().replace(/^>\s?/, ''));
        i += 1;
      }
      out.push(`<blockquote class="md-quote">${inline(quote.join('<br/>'))}</blockquote>`);
      continue;
    }

    if (/^[-*•]\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*•]\s+/.test(lines[i].trim())) {
        items.push(`<li class="md-li">${inline(lines[i].trim().replace(/^[-*•]\s+/, ''))}</li>`);
        i += 1;
      }
      out.push(`<ul class="md-ul">${items.join('')}</ul>`);
      continue;
    }

    if (/^\d+\.\s+/.test(trimmed)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        items.push(`<li class="md-li">${inline(lines[i].trim().replace(/^\d+\.\s+/, ''))}</li>`);
        i += 1;
      }
      out.push(`<ol class="md-ol">${items.join('')}</ol>`);
      continue;
    }

    // 普通段落：合并到空行
    const para: string[] = [trimmed];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() &&
      !lines[i].trim().startsWith('#') &&
      !lines[i].trim().startsWith('|') &&
      !/^[-*•]\s+/.test(lines[i].trim()) &&
      !/^\d+\.\s+/.test(lines[i].trim()) &&
      !/^>\s?/.test(lines[i].trim()) &&
      !/^%%FENCE_/.test(lines[i].trim()) &&
      !/^---+$/.test(lines[i].trim())
    ) {
      para.push(lines[i].trim());
      i += 1;
    }
    out.push(`<p class="md-p">${inline(para.join('<br/>'))}</p>`);
  }

  return out.join('\n');
}
