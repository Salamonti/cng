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

    function nextNonEmptyTrimmed(fromIdx) {
      for (let j = fromIdx; j < lines.length; j++) {
        const t = lines[j].trim();
        if (t !== '') return t;
      }
      return null;
    }

    const out = [];
    let inList = false;
    let inOl = false;
    let inTable = false;
    /** Collect plain lines into one &lt;p&gt; joined by &lt;br&gt; — avoids stacked &lt;p&gt; margins (double spacing). */
    const paraBuf = [];

    function flushPara() {
      if (!paraBuf.length) return;
      out.push('<p>' + paraBuf.map((ln) => inlineFormat(ln)).join('<br>') + '</p>');
      paraBuf.length = 0;
    }

    function closeUnorderedList() {
      if (inList) {
        out.push('</ul>');
        inList = false;
      }
    }

    function closeOrderedList() {
      if (inOl) {
        out.push('</ol>');
        inOl = false;
      }
    }

    /** Visual gap before a section heading when there is prior content (blank line in source optional). */
    function emitHeading(level, titleText) {
      closeList();
      flushPara();
      closeTable();
      if (out.length > 0) {
        out.push('<div class="note-md-preheading-gap" aria-hidden="true"></div>');
      }
      out.push(`<h${level}>${inlineFormat(titleText)}</h${level}>`);
    }

    function closeList() {
      closeUnorderedList();
      closeOrderedList();
    }

    /** Append a wrapped / continuation line to the most recent &lt;li&gt;…&lt;/li&gt; block (same list, keeps browser 1,2,3…).
     *  Matches both plain <li> and attributed <li value="N"> elements. */
    function appendContinuationToLastLi(rawLine) {
      const fmt = '<br>' + inlineFormat(rawLine);
      for (let k = out.length - 1; k >= 0; k--) {
        const s = out[k];
        if (typeof s !== 'string') continue;
        if (/^<li( [^>]*)?>[\s\S]*<\/li>$/.test(s)) {
          out[k] = s.replace(/<\/li>$/, fmt + '</li>');
          return true;
        }
      }
      return false;
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
        // Blank lines must NOT split a list when the next non-empty line continues the same list.
        // Otherwise each fragment becomes its own <ol>, and every segment shows "1." in the browser.
        const nextLine = nextNonEmptyTrimmed(i + 1);
        const olNextRe =
          /^\*{2}[0-9\uFF10-\uFF19]{1,4}[.\uFF0E]\*{2}\s*\S|^\*{2}[0-9\uFF10-\uFF19]{1,4}\)\*{2}\s*\S|^[0-9\uFF10-\uFF19]{1,4}[.\uFF0E]\s*\S|^[0-9\uFF10-\uFF19]{1,4}\)\s*\S/;
        if (inOl && nextLine && olNextRe.test(nextLine)) {
          continue;
        }
        // Also keep an ordered list open when the next non-empty line is explanatory prose
        // (not a new heading, HR, or bullet list start) — handles blank line between a bold
        // list-item header and the explanatory paragraph that belongs to it.
        if (inOl && nextLine && !/^#{1,6}\s/.test(nextLine) && !/^[-*]{3,}$/.test(nextLine) && !/^[-*]\s+/.test(nextLine)) {
          continue;
        }
        if (inList && nextLine && /^[-*]\s+/.test(nextLine)) {
          continue;
        }
        closeList();
        closeTable();
        flushPara();
        continue;
      }

      if (line.includes('|')) {
        const next = (lines[i + 1] || '').trim();
        if (!inTable && next && isTableSeparator(next)) {
          closeList();
          flushPara();
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
        flushPara();
        closeTable();
        out.push('<hr>');
        continue;
      }

      const h6 = line.match(/^######\s+(.+)$/);
      if (h6) {
        emitHeading(6, h6[1]);
        continue;
      }

      const h5 = line.match(/^#####\s+(.+)$/);
      if (h5) {
        emitHeading(5, h5[1]);
        continue;
      }

      const h4 = line.match(/^####\s+(.+)$/);
      if (h4) {
        emitHeading(4, h4[1]);
        continue;
      }

      const h3 = line.match(/^###\s+(.+)$/);
      if (h3) {
        emitHeading(3, h3[1]);
        continue;
      }

      const h2 = line.match(/^##\s+(.+)$/);
      if (h2) {
        emitHeading(2, h2[1]);
        continue;
      }

      const h1 = line.match(/^#\s+(.+)$/);
      if (h1) {
        emitHeading(1, h1[1]);
        continue;
      }

      /* Honor LLM numbering verbatim: capture the number and emit <ol start=N>/<li value=N>
         so each section's list shows exactly what the model wrote (A: 1,2 then B: 1,2). */
      const digitClass = '[0-9\uFF10-\uFF19]';
      let ordered =
        line.match(new RegExp(`^\\*{2}(${digitClass}{1,4})[.\\uFF0E]\\*{2}\\s*(.+)$`)) ||
        line.match(new RegExp(`^\\*{2}(${digitClass}{1,4})\\)\\*{2}\\s*(.+)$`)) ||
        line.match(new RegExp(`^(${digitClass}{1,4})[.\\uFF0E]\\s*(.+)$`)) ||
        line.match(new RegExp(`^(${digitClass}{1,4})\\)\\s*(.+)$`));
      if (ordered) {
        flushPara();
        closeUnorderedList();
        const num = parseInt(
          ordered[1].replace(/[\uFF10-\uFF19]/g, (c) => String(c.charCodeAt(0) - 0xFF10)),
          10,
        );
        const safeNum = Number.isFinite(num) && num >= 0 ? num : 1;
        if (!inOl) {
          out.push(`<ol start="${safeNum}">`);
          inOl = true;
        }
        out.push(`<li value="${safeNum}">${inlineFormat(ordered[2])}</li>`);
        continue;
      }

      const bullet = line.match(/^[-*]\s+(.+)$/);
      if (bullet) {
        flushPara();
        closeOrderedList();
        if (!inList) {
          out.push('<ul>');
          inList = true;
        }
        out.push(`<li>${inlineFormat(bullet[1])}</li>`);
        continue;
      }

      // Numbered / bullet continuations: LLMs often insert blank lines, sub-bullets, or
      // unnumbered lines between "1." and "2." — closing the list there produced many
      // one-item <ol>s (every segment rendered as "1.").
      if (inOl || inList) {
        if (appendContinuationToLastLi(line)) {
          continue;
        }
      }

      closeList();
      paraBuf.push(line);
    }

    closeList();
    closeTable();
    flushPara();
    return out.join('');
  }

  window.CNGMarkdown = { renderMarkdownSimple };
})();
