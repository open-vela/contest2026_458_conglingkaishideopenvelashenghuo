#!/usr/bin/env python3
"""把大赛技术报告 .docx 转成排版规整的 HTML（再由 Chrome 打印成 PDF）。

为什么不用现成转换器：本机没有 Word / LibreOffice，而 editor_sdk 不提供
PDF 导出。docx → HTML → Chrome --print-to-pdf 是唯一能在这台机器上
产出「中文、表格、标题层级都正常」的 PDF 的路径。

    python docx2html.py <input.docx> <output.html> [--title "..."]
"""
import argparse
import html
import re
import sys

import docx
from docx.table import Table
from docx.text.paragraph import Paragraph

CSS = """
@page { size: A4; margin: 18mm 16mm 18mm 16mm; }
* { box-sizing: border-box; }
body {
  font-family: "Microsoft YaHei", "PingFang SC", "Source Han Sans SC",
               "Noto Sans CJK SC", "SimSun", sans-serif;
  font-size: 10.5pt; line-height: 1.75; color: #1a1a1a;
  margin: 0; padding: 0;
}
h1 {
  font-size: 19pt; text-align: center; margin: 0 0 6pt 0;
  letter-spacing: .5px; color: #0d3b66;
}
h1 + .subtitle { text-align: center; color: #5a6b7b; font-size: 10pt;
  margin-bottom: 18pt; }
h2 {
  font-size: 13.5pt; color: #0d3b66; margin: 20pt 0 8pt 0;
  padding-left: 8pt; border-left: 4px solid #2f6fb3;
  page-break-after: avoid;
}
h3 {
  font-size: 11.5pt; color: #16456f; margin: 14pt 0 6pt 0;
  page-break-after: avoid;
}
h4 { font-size: 10.5pt; color: #16456f; margin: 12pt 0 4pt 0;
  page-break-after: avoid; }
p { margin: 5pt 0; text-align: justify; }
p.meta { color: #444; background: #f4f7fa; border-left: 3px solid #9dbfe0;
  padding: 6pt 9pt; margin: 8pt 0; font-size: 10pt; }
table {
  border-collapse: collapse; width: 100%; margin: 8pt 0 12pt 0;
  font-size: 9.5pt; page-break-inside: auto;
}
th, td {
  border: .6pt solid #9aa7b4; padding: 4pt 6pt; vertical-align: top;
  text-align: left; word-break: break-word;
}
th { background: #e8f0f8; font-weight: 600; color: #0d3b66; }
tr { page-break-inside: avoid; }
code, .mono {
  font-family: Consolas, "Courier New", monospace; font-size: 9.5pt;
  background: #f2f4f7; padding: 0 2pt; border-radius: 2px;
}
pre {
  font-family: Consolas, "Courier New", monospace; font-size: 9pt;
  background: #f6f8fa; border: .6pt solid #d6dde5; border-radius: 3px;
  padding: 7pt 9pt; white-space: pre-wrap; word-break: break-all;
  line-height: 1.5; margin: 8pt 0;
}
strong { color: #0b2d4d; }
.note { color: #6b5b00; background: #fffbe6; border: .6pt solid #f0e0a0;
  padding: 6pt 9pt; margin: 8pt 0; font-size: 10pt; border-radius: 3px; }
footer { margin-top: 22pt; padding-top: 8pt; border-top: .6pt solid #cbd5e0;
  font-size: 9pt; color: #7b8794; }
"""


def iter_blocks(doc):
    """按文档顺序产出段落与表格（python-docx 的 .paragraphs/.tables 会丢顺序）"""
    body = doc.element.body
    for child in body.iterchildren():
        tag = child.tag.split('}')[-1]
        if tag == 'p':
            yield Paragraph(child, doc)
        elif tag == 'tbl':
            yield Table(child, doc)


def heading_level(p):
    """从段落样式名/大纲级别推断标题层级；0 表示普通正文"""
    name = (p.style.name or '') if p.style is not None else ''
    m = re.search(r'(?:Heading|标题)\s*(\d)', name, re.I)
    if m:
        return int(m.group(1))
    if re.search(r'^Title$|^标题$', name, re.I):
        return 1
    # 中文文档常见的 "1、" / "3.4 " 手工编号小标题
    txt = p.text.strip()
    if re.match(r'^[一二三四五六七八九十]+、', txt) and len(txt) < 40:
        return 2
    if re.match(r'^\d+\.\d+(\.\d+)?\s', txt) and len(txt) < 40:
        return 3
    if re.match(r'^\d+、[^，。]{0,30}$', txt):
        return 2
    return 0


def para_html(p):
    txt = p.text.strip()
    if not txt:
        return ''
    level = heading_level(p)
    if level == 1:
        esc = html.escape(txt)
        return f'<h1>{esc}</h1>'
    if level == 2:
        return f'<h2>{html.escape(txt)}</h2>'
    if level == 3:
        return f'<h3>{html.escape(txt)}</h3>'
    if level >= 4:
        return f'<h4>{html.escape(txt)}</h4>'

    # 保留行内加粗
    out = []
    for run in p.runs:
        t = html.escape(run.text)
        if not t:
            continue
        if run.bold:
            t = f'<strong>{t}</strong>'
        out.append(t)
    inner = ''.join(out) or html.escape(txt)

    if txt.startswith('说明：') or txt.startswith('提交方式：') \
            or txt.startswith('项目源码与'):
        return f'<p class="meta">{inner}</p>'
    if txt.startswith(('注意：', '⚠️', '大赛仅', '作品须', '若含', 'AI Coding 日志')):
        return f'<p class="note">{inner}</p>'
    if re.match(r'^[a-z]\)', txt) or txt.startswith('- '):
        return f'<p style="margin-left:14pt">{inner}</p>'
    return f'<p>{inner}</p>'


def table_html(t):
    rows = []
    for ri, row in enumerate(t.rows):
        cells = []
        seen = set()
        for cell in row.cells:
            if id(cell._tc) in seen:      # 合并单元格会重复出现
                continue
            seen.add(id(cell._tc))
            txt = '\n'.join(x.strip() for x in cell.text.split('\n') if x.strip())
            tag = 'th' if ri == 0 else 'td'
            cells.append(f'<{tag}>{html.escape(txt).replace(chr(10), "<br>")}</{tag}>')
        rows.append('<tr>' + ''.join(cells) + '</tr>')
    return '<table>' + ''.join(rows) + '</table>'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('src')
    ap.add_argument('dst')
    ap.add_argument('--title', default='')
    ap.add_argument('--subtitle', default='')
    ap.add_argument('--footer', default='')
    ap.add_argument('--h1', default='', help='覆盖文档首段主标题文本')
    args = ap.parse_args()

    doc = docx.Document(args.src)
    parts = []
    for blk in iter_blocks(doc):
        if isinstance(blk, Paragraph):
            h = para_html(blk)
            if h:
                parts.append(h)
        else:
            parts.append(table_html(blk))

    sub = ''
    if args.subtitle:
        sub = f'<p class="subtitle">{html.escape(args.subtitle)}</p>'

    # 文档首段通常是大标题，但手工排版的 docx 不一定带 Title 样式 —— 没有 h1
    # 时把第一段升级成 h1，否则整篇报告会没有主标题。
    if not any(p.startswith('<h1') for p in parts):
        for i, p in enumerate(parts):
            if p.startswith('<p'):
                parts[i] = '<h1>' + p[p.index('>') + 1:].replace(
                    '</p>', '</h1>', 1)
                break
    if sub:
        for i, p in enumerate(parts):
            if p.startswith('<h1'):
                parts.insert(i + 1, sub)
                break
        else:
            parts.insert(0, sub)

    if args.h1:
        for i, p in enumerate(parts):
            if p.startswith('<h1'):
                parts[i] = f'<h1>{html.escape(args.h1)}</h1>'
                break
    else:
        # h1 里如果是模板原文的加粗标记，去掉标签保证标题干净
        for i, p in enumerate(parts):
            if p.startswith('<h1'):
                m = re.match(r'<h1>(.*)</h1>$', p, re.S)
                if m and '<' in m.group(1):
                    parts[i] = '<h1>' + re.sub(r'<[^>]+>', '', m.group(1)) + '</h1>'
                break

    foot = f'<footer>{html.escape(args.footer)}</footer>' if args.footer else ''

    page = ('<!DOCTYPE html><html lang="zh-CN"><head><meta charset="utf-8">'
            f'<title>{html.escape(args.title or "report")}</title>'
            f'<style>{CSS}</style></head><body>'
            + ''.join(parts) + foot + '</body></html>')
    with open(args.dst, 'w', encoding='utf-8') as fh:
        fh.write(page)
    print('wrote %s (%d blocks, %d chars)'
          % (args.dst, len(parts), len(page)))


if __name__ == '__main__':
    sys.exit(main())
