(function () {
  function renderMarkdownSimple(text) {
    const raw = text == null ? '' : String(text);
    const escaped = raw
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/\r\n/g, '\n');

    function inlineFormat(s) {
      return s
        .replace(/`([^`]+)`/g, '<code>$1</code>')
        .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
        .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1<em>$2</em>');
    }

    const lines = escaped.split('\n');
    const out = [];
    let inList = false;
    let inTable = false;

    function closeList() {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
    }

    function closeTable() {
      if (inTable) {
        out.push('</tbody></table>');
        inTable = false;
      }
    }

    function isTableSeparator(line) {
      return /^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?$/.test(line);
    }

    function parseRow(line) {
      return line
        .replace(/^\|/, '')
        .replace(/\|$/, '')
        .split('|')
        .map(cell => inlineFormat(cell.trim()));
    }

    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].trim();

      if (!line) {
        closeList();
        closeTable();
        continue;
      }

      if (line.includes('|')) {
        const next = (lines[i + 1] || '').trim();
        if (!inTable && next && isTableSeparator(next)) {
          closeList();
          const headers = parseRow(line);
          out.push('<table><thead><tr>' + headers.map(h => `<th>${h}</th>`).join('') + '</tr></thead><tbody>');
          inTable = true;
          i += 1;
          continue;
        }
        if (inTable) {
          const cells = parseRow(line);
          out.push('<tr>' + cells.map(c => `<td>${c}</td>`).join('') + '</tr>');
          continue;
        }
      }

      closeTable();

      const hr = line.match(/^(-{3,}|\*{3,})$/);
      if (hr) {
        closeList();
        out.push('<hr>');
        continue;
      }

      const h6 = line.match(/^######\s+(.+)$/);
      if (h6) {
        closeList();
        out.push(`<h6>${inlineFormat(h6[1])}</h6>`);
        continue;
      }

      const h5 = line.match(/^#####\s+(.+)$/);
      if (h5) {
        closeList();
        out.push(`<h5>${inlineFormat(h5[1])}</h5>`);
        continue;
      }

      const h4 = line.match(/^####\s+(.+)$/);
      if (h4) {
        closeList();
        out.push(`<h4>${inlineFormat(h4[1])}</h4>`);
        continue;
      }

      const h3 = line.match(/^###\s+(.+)$/);
      if (h3) {
        closeList();
        out.push(`<h3>${inlineFormat(h3[1])}</h3>`);
        continue;
      }

      const h2 = line.match(/^##\s+(.+)$/);
      if (h2) {
        closeList();
        out.push(`<h2>${inlineFormat(h2[1])}</h2>`);
        continue;
      }

      const h1 = line.match(/^#\s+(.+)$/);
      if (h1) {
        closeList();
        out.push(`<h1>${inlineFormat(h1[1])}</h1>`);
        continue;
      }

      const bullet = line.match(/^[-*]\s+(.+)$/);
      if (bullet) {
        if (!inList) {
          out.push('<ul>');
          inList = true;
        }
        out.push(`<li>${inlineFormat(bullet[1])}</li>`);
        continue;
      }

      closeList();
      out.push(`<p>${inlineFormat(line)}</p>`);
    }

    closeList();
    closeTable();
    return out.join('');
  }

  window.CNGMarkdown = { renderMarkdownSimple };
})();
