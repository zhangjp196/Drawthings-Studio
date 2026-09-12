// 对话消息的 Markdown 渲染（轻量自实现：标题/加粗/斜体/行内码/代码块/列表/表格/引用/分割线/链接）。
// 先整体 HTML 转义、再做语法替换——天然防 XSS；代码块带一键复制按钮。
(function () {
  function buildList(items) {
    // 按缩进支持嵌套的有序/无序列表
    let html = '';
    const stack = [];
    for (const it of items) {
      while (stack.length) {
        const top = stack[stack.length - 1];
        if (top.indent > it.indent || (top.indent === it.indent && top.type !== it.type)) {
          if (top.liOpen) { html += '</li>'; top.liOpen = false; }
          html += '</' + top.type + '>';
          stack.pop();
        } else break;
      }
      if (!stack.length || stack[stack.length - 1].indent < it.indent) {
        stack.push({ type: it.type, indent: it.indent, liOpen: false });
        html += '<' + it.type + '>';
      } else if (stack[stack.length - 1].liOpen) {
        html += '</li>';
        stack[stack.length - 1].liOpen = false;
      }
      html += '<li>' + mdInline(it.text);
      stack[stack.length - 1].liOpen = true;
    }
    while (stack.length) {
      const s = stack.pop();
      if (s.liOpen) html += '</li>';
      html += '</' + s.type + '>';
    }
    return html;
  }

  function mdInline(s) {
    return s
      .replace(/`([^`]+)`/g, '<code>$1</code>')
      .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[\s(（])\*([^*\n]+)\*(?=[\s)）.,;:!?]|$)/g, '$1<em>$2</em>')
      .replace(/~~([^~]+)~~/g, '<del>$1</del>')
      .replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener">$1</a>');
  }

  function renderMd(raw) {
    if (!raw) return '';
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const lines = esc(raw).split('\n');
    const out = [];
    const isTableSep = l => /^[\s|:-]+$/.test(l) && l.includes('-') && l.includes('|');
    const isSpecial = l => /^\s*(#{1,6}\s|```|&gt;|\|.*\|\s*$|[-*+]\s+|\d+[.)]\s+)/.test(l);
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];
      if (/^\s*```/.test(line)) {
        const buf = []; i++;
        while (i < lines.length && !/^\s*```/.test(lines[i])) { buf.push(lines[i]); i++; }
        i++;
        out.push('<div class="code-wrap"><button type="button" class="copy-code">' + I18N.t('common.copy') + '</button>'
                 + '<pre><code>' + buf.join('\n') + '</code></pre></div>');
        continue;
      }
      const h = line.match(/^(#{1,6})\s+(.*)$/);
      if (h) { out.push('<h' + h[1].length + '>' + mdInline(h[2].trim()) + '</h' + h[1].length + '>'); i++; continue; }
      if (/^\s*([-*_])\s*(\1\s*){2,}$/.test(line)) { out.push('<hr>'); i++; continue; }
      if (/^\s*&gt;/.test(line)) {
        const buf = [];
        while (i < lines.length && /^\s*&gt;/.test(lines[i])) { buf.push(lines[i].replace(/^\s*&gt;\s?/, '')); i++; }
        out.push('<blockquote>' + buf.map(l => mdInline(l)).join('<br>') + '</blockquote>');
        continue;
      }
      if (/^\s*\|.*\|\s*$/.test(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        const parse = r => r.trim().replace(/^\||\|$/g, '').split('|').map(c => mdInline(c.trim()));
        const head = parse(line); i += 2;
        const rows = [];
        while (i < lines.length && /^\s*\|.*\|\s*$/.test(lines[i])) { rows.push(parse(lines[i])); i++; }
        let t = '<table><thead><tr>' + head.map(c => '<th>' + c + '</th>').join('') + '</tr></thead><tbody>';
        t += rows.map(r => '<tr>' + head.map((_, ci) => '<td>' + (r[ci] || '') + '</td>').join('') + '</tr>').join('');
        out.push(t + '</tbody></table>');
        continue;
      }
      if (/^\s*(\d+[.)]|[-*+])\s+/.test(line)) {
        const items = [];
        while (i < lines.length) {
          const m = lines[i].match(/^(\s*)(\d+[.)]|[-*+])\s+(.*)$/);
          if (!m) break;
          items.push({ indent: m[1].replace(/\t/g, '  ').length,
                       type: /^\d/.test(m[2]) ? 'ol' : 'ul', text: m[3] });
          i++;
        }
        out.push(buildList(items));
        continue;
      }
      if (!line.trim()) { i++; continue; }
      const buf = [line]; i++;
      while (i < lines.length && lines[i].trim() && !isSpecial(lines[i]) && !isTableSep(lines[i])) { buf.push(lines[i]); i++; }
      out.push('<p>' + buf.map(l => mdInline(l.trim())).join('<br>') + '</p>');
    }
    return out.join('\n');
  }

  window.renderMd = renderMd;
})();
