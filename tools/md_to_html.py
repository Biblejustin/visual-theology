#!/usr/bin/env python3
"""Tiny Markdown → standalone HTML converter for sermon deliverables.

No external dependencies. Handles the subset used in our prep docs:
h1-h4, bold, italic, blockquote, hr, lists, paragraphs, inline code,
horizontal rules. Output is a complete HTML5 doc with print-friendly
CSS so Pages opens it formatted and Save-as-PDF from a browser works
out of the box.

Usage:
    python3 tools/md_to_html.py FILE.md [FILE.md ...]
    # writes FILE.html next to each input

A single CSS block targets sermon-print use: serif type, generous line
height, page-break-friendly headings, blockquotes for the THT, and
visible underscores for fill-in-blank handouts.
"""
import html
import re
import sys
from pathlib import Path


_BASE_CSS = """
@page { margin: 0.75in; }
:root { color-scheme: light dark; }
body {
  font-family: "Source Serif 4", "Source Serif Pro", Georgia, "Times New Roman", serif;
  font-size: 12pt; line-height: 1.55; color: #222;
  max-width: 7in; margin: 1.25rem auto; padding: 0 1rem;
  background: #fff;
}
h1 { font-size: 22pt; margin: 1.5em 0 0.4em; line-height: 1.2;
     border-bottom: 2px solid #888; padding-bottom: 0.2em; }
h2 { font-size: 16pt; margin: 1.4em 0 0.4em; line-height: 1.25;
     break-before: avoid; }
h3 { font-size: 13pt; margin: 1.2em 0 0.3em; font-weight: 600; }
h4 { font-size: 12pt; margin: 1em 0 0.3em; font-style: italic; font-weight: 500; }
p, li { margin: 0.5em 0; }
ul, ol { padding-left: 1.5em; }
blockquote {
  margin: 1em 0; padding: 0.5em 1em; border-left: 3px solid #999;
  font-style: italic; background: #f6f4ee;
}
hr { border: none; border-top: 1px solid #bbb; margin: 1.5em 0; }
strong { font-weight: 600; }
em { font-style: italic; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em;
       background: #f0eee8; padding: 0.05em 0.3em; border-radius: 3px; }
.under {
  display: inline-block; min-width: 8em;
  border-bottom: 1.5px solid #444;
  padding: 0 0.2em;
  line-height: 1.1;
}
.pagebreak { break-after: page; page-break-after: always; height: 0; }
@media print {
  body { max-width: none; margin: 0; padding: 0; }
  h2, h3 { page-break-after: avoid; }
  blockquote { page-break-inside: avoid; }
}
"""

# Tight handout profile: pew bulletin must fit two sides of one sheet.
# Applied when the input filename contains "handout", "congregation", or "pew".
_HANDOUT_CSS = """
@page { margin: 0.5in; }
:root { color-scheme: light dark; }
body {
  font-family: "Source Serif 4", "Source Serif Pro", Georgia, "Times New Roman", serif;
  font-size: 10.5pt; line-height: 1.32; color: #222;
  max-width: 7.5in; margin: 0.6rem auto; padding: 0 0.5rem;
  background: #fff;
}
h1 { font-size: 17pt; margin: 0.3em 0 0.2em; line-height: 1.15;
     border-bottom: 1.5px solid #888; padding-bottom: 0.1em; }
h2 { font-size: 12.5pt; margin: 0.7em 0 0.2em; line-height: 1.2;
     page-break-after: avoid; }
h3 { font-size: 11pt; margin: 0.55em 0 0.15em; font-weight: 600;
     page-break-after: avoid; }
h4 { font-size: 10.5pt; margin: 0.45em 0 0.15em; font-style: italic;
     font-weight: 500; page-break-after: avoid; }
p { margin: 0.3em 0; }
ul, ol { padding-left: 1.2em; margin: 0.3em 0; }
li { margin: 0.15em 0; }
blockquote {
  margin: 0.5em 0; padding: 0.3em 0.7em; border-left: 2.5px solid #999;
  font-style: italic; background: #f6f4ee; page-break-inside: avoid;
}
hr { border: none; border-top: 1px solid #bbb; margin: 0.7em 0; }
strong { font-weight: 600; }
em { font-style: italic; }
code { font-family: ui-monospace, Menlo, monospace; font-size: 0.9em;
       background: #f0eee8; padding: 0.05em 0.3em; border-radius: 3px; }
.under {
  display: inline-block; min-width: 6em;
  border-bottom: 1.2px solid #444;
  padding: 0 0.2em; line-height: 1.05;
}
.pagebreak { break-after: page; page-break-after: always; height: 0; }
@media print {
  body { max-width: none; margin: 0; padding: 0; }
  h2, h3, h4 { page-break-after: avoid; }
  blockquote { page-break-inside: avoid; }
}
"""


def _pick_css(stem: str) -> str:
    s = stem.lower()
    if any(k in s for k in ("handout", "congregation", "pew", "bulletin")):
        return _HANDOUT_CSS
    return _BASE_CSS


def _inline(text):
    """Inline markdown: **bold**, *italic*, `code`. Escape HTML first."""
    text = html.escape(text, quote=False)
    # underline-runs (4+ underscores) → fill-in-blank spans
    text = re.sub(r"_{4,}", lambda m: f'<span class="under">{"&nbsp;" * (len(m.group(0)) // 2)}</span>', text)
    # bold then italic (order matters)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", text)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    return text


def md_to_html_body(md):
    lines = md.splitlines()
    out = []
    i = 0
    n = len(lines)

    def close(stack):
        while stack:
            out.append(f"</{stack.pop()}>")

    stack = []  # tracks open list elements

    while i < n:
        line = lines[i]
        stripped = line.strip()

        # blank line: close any open list and continue
        if not stripped:
            close(stack)
            i += 1
            continue

        # page break marker (handout-friendly forced break for printing)
        if re.fullmatch(r"<!--\s*PAGEBREAK\s*-->", stripped, re.I):
            close(stack)
            out.append('<div class="pagebreak"></div>')
            i += 1
            continue

        # horizontal rule
        if re.fullmatch(r"-{3,}|\*{3,}|_{3,}", stripped):
            close(stack)
            out.append("<hr>")
            i += 1
            continue

        # heading
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            close(stack)
            level = len(m.group(1))
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            i += 1
            continue

        # blockquote (contiguous)
        if stripped.startswith(">"):
            close(stack)
            block = []
            while i < n and lines[i].lstrip().startswith(">"):
                block.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append("<blockquote>" + _inline(" ".join(block).strip()) + "</blockquote>")
            continue

        # unordered list
        if re.match(r"^[-*+]\s+", stripped):
            if not stack or stack[-1] != "ul":
                close(stack); out.append("<ul>"); stack.append("ul")
            item = re.sub(r"^[-*+]\s+", "", stripped)
            out.append(f"<li>{_inline(item)}</li>")
            i += 1
            continue

        # ordered list
        if re.match(r"^\d+\.\s+", stripped):
            if not stack or stack[-1] != "ol":
                close(stack); out.append("<ol>"); stack.append("ol")
            item = re.sub(r"^\d+\.\s+", "", stripped)
            out.append(f"<li>{_inline(item)}</li>")
            i += 1
            continue

        # paragraph: consume contiguous non-empty, non-special lines
        close(stack)
        para = [stripped]
        i += 1
        while i < n:
            nxt = lines[i].strip()
            if not nxt:
                break
            if re.match(r"^(#{1,6})\s|^[-*+]\s|^\d+\.\s|^>|^-{3,}$", nxt):
                break
            para.append(nxt)
            i += 1
        out.append("<p>" + _inline(" ".join(para)) + "</p>")

    close(stack)
    return "\n".join(out)


def md_to_html_string(md: str, stem: str) -> str:
    """Convert an in-memory markdown string to a standalone HTML document.
    Picks the tight handout profile when `stem` contains the handout
    keyword (same rule as file-based convert)."""
    title_match = re.search(r"^#\s+(.+)$", md, re.M)
    title = title_match.group(1).strip() if title_match else stem
    body = md_to_html_body(md)
    css = _pick_css(stem)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{html.escape(title)}</title>
<style>{css}</style>
</head>
<body>
{body}
</body>
</html>
"""


def convert(md_path: Path) -> Path:
    md = md_path.read_text(encoding="utf-8")
    html_doc = md_to_html_string(md, md_path.stem)
    out = md_path.with_suffix(".html")
    out.write_text(html_doc, encoding="utf-8")
    return out


def main(argv):
    if len(argv) < 2:
        print("usage: md_to_html.py FILE.md [FILE.md ...]", file=sys.stderr)
        return 2
    for p in argv[1:]:
        path = Path(p)
        if not path.exists():
            print(f"skip: {p} not found", file=sys.stderr)
            continue
        out = convert(path)
        print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
