# -*- coding: utf-8 -*-
"""Markdown 文集 EPUB 制作工具（图形界面 + 命令行）。"""

from __future__ import annotations

import argparse
import html
import mimetypes
import re
import struct
import uuid
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable
from xml.etree import ElementTree as ET


TITLE_RE = re.compile(r"^####\s+(?:标题[:：]\s*)?(.+?)\s*$")
AUTHOR_RE = re.compile(r"^作者\s*[:：]?\s*(.+)$")
INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


@dataclass
class Article:
    title: str
    author: str
    paragraphs: list[str]


@dataclass
class BookOptions:
    source: Path
    cover: Path
    style: Path
    output: Path
    title: str
    creator: str
    css_override: str | None = None


def esc(text: str) -> str:
    return html.escape(text, quote=False)


def parse_articles(source: Path) -> list[Article]:
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    title_indices = [i for i, line in enumerate(lines) if TITLE_RE.match(line.strip())]
    articles: list[Article] = []
    for number, start in enumerate(title_indices):
        match = TITLE_RE.match(lines[start].strip())
        assert match is not None
        end = title_indices[number + 1] if number + 1 < len(title_indices) else len(lines)
        author = ""
        paragraphs: list[str] = []
        for line in lines[start + 1 : end]:
            stripped = line.strip()
            clean = stripped.strip("*").strip().replace("\u00a0", " ")
            if not author and not paragraphs and clean:
                author_match = AUTHOR_RE.match(clean)
                if author_match:
                    author = author_match.group(1).strip()
                    continue
            if stripped and stripped not in {"聽", "&nbsp;"}:
                paragraphs.append(stripped)
        articles.append(Article(match.group(1).strip(), author, paragraphs))
    return articles


def inline(text: str) -> str:
    parts = INLINE_BOLD_RE.split(text)
    return "".join(
        "<strong>%s</strong>" % esc(part) if index % 2 else esc(part)
        for index, part in enumerate(parts)
    )


def render_paragraphs(paragraphs: Iterable[str]) -> str:
    result = []
    for paragraph in paragraphs:
        bold_line = re.fullmatch(r"\*\*(.+)\*\*", paragraph)
        if bold_line:
            result.append('<p class="section-title"><strong>%s</strong></p>' % esc(bold_line.group(1)))
        else:
            result.append("<p>%s</p>" % inline(paragraph))
    return "\n".join(result)


def xhtml(title: str, body: str, body_class: str = "") -> str:
    return f'''<?xml version="1.0" encoding="utf-8"?>
<!DOCTYPE html>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" lang="zh-CN" xml:lang="zh-CN">
<head><meta charset="utf-8"/><title>{esc(title)}</title><link rel="stylesheet" type="text/css" href="style.css"/></head>
<body class="{body_class}">{body}</body></html>'''


def image_info(path: Path) -> tuple[str, str, int, int]:
    data = path.read_bytes()
    suffix = path.suffix.lower()
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = struct.unpack(">II", data[16:24])
        return "cover.png", "image/png", width, height
    if data.startswith(b"\xff\xd8"):
        i = 2
        while i + 9 < len(data):
            if data[i] != 0xFF:
                i += 1
                continue
            marker = data[i + 1]
            if marker in range(0xC0, 0xC4):
                height, width = struct.unpack(">HH", data[i + 5 : i + 9])
                return "cover.jpg", "image/jpeg", width, height
            if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                i += 2
            else:
                i += 2 + struct.unpack(">H", data[i + 2 : i + 4])[0]
        raise ValueError("无法读取 JPEG 封面尺寸")
    mime = mimetypes.guess_type(path.name)[0]
    raise ValueError(f"暂不支持此封面格式：{suffix or mime or '未知格式'}；请使用 JPG 或 PNG")


def extract_css(path: Path) -> str:
    text = path.read_text(encoding="utf-8-sig")
    match = re.search(r"```(?:plain|css)?\s*\n(.*?)```", text, re.S)
    css = match.group(1) if match else text
    return re.sub(r"\n{3,}", "\n\n", css).strip() + "\n"


def build_epub(options: BookOptions) -> tuple[int, Path]:
    for path, label in ((options.source, "原稿"), (options.cover, "封面"), (options.style, "样式表")):
        if not path.is_file():
            raise FileNotFoundError(f"{label}文件不存在：{path}")
    articles = parse_articles(options.source)
    if not articles:
        raise ValueError("没有识别到文章。每篇文章需以 Markdown 标题（例如 #### 标题：文章名）开始。")
    missing_authors = [a.title for a in articles if not a.author]
    empty_articles = [a.title for a in articles if not a.paragraphs]
    if missing_authors:
        raise ValueError("以下文章缺少作者：" + "、".join(missing_authors))
    if empty_articles:
        raise ValueError("以下文章正文为空：" + "、".join(empty_articles))

    cover_name, cover_mime, cover_width, cover_height = image_info(options.cover)
    book_id = str(uuid.uuid4())
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    options.output.parent.mkdir(parents=True, exist_ok=True)

    chapter_items = []
    spine_items = []
    toc_rows = []
    nav_rows = ['<li><a href="chapter_00.xhtml">目录</a></li>']
    ncx_points = ['<navPoint id="np0" playOrder="1"><navLabel><text>目录</text></navLabel><content src="chapter_00.xhtml"/></navPoint>']
    chapter_files: list[tuple[str, str]] = []

    for index, article in enumerate(articles, 1):
        filename = f"chapter_{index:02d}.xhtml"
        chapter_items.append(f'<item id="ch{index:02d}" href="{filename}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="ch{index:02d}"/>')
        label = f"{article.title}  /{article.author}"
        toc_rows.append(f'<p class="toc-item"><a class="toc-title" href="{filename}">{esc(label)}</a></p>')
        nav_rows.append(f'<li><a href="{filename}">{esc(label)}</a></li>')
        ncx_points.append(f'<navPoint id="np{index}" playOrder="{index + 1}"><navLabel><text>{esc(label)}</text></navLabel><content src="{filename}"/></navPoint>')
        body = f'<section class="chapter"><h1 class="chapter-title">{esc(article.title)}</h1><p class="author">{esc(article.author)}</p><div class="content">{render_paragraphs(article.paragraphs)}</div></section>'
        chapter_files.append((filename, xhtml(article.title, body)))

    toc_body = '<section class="chapter toc"><h1 class="chapter-title toc-header">目录</h1><div class="toc-list">' + "".join(toc_rows) + "</div></section>"
    nav_body = '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>' + "".join(nav_rows) + "</ol></nav>"
    cover_body = f'<div class="cover"><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {cover_width} {cover_height}" preserveAspectRatio="xMidYMid meet"><image width="{cover_width}" height="{cover_height}" xlink:href="images/{cover_name}"/></svg></div>'

    manifest = "".join(chapter_items)
    spine = "".join(spine_items)
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookID" xml:lang="zh-CN"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="BookID">urn:uuid:{book_id}</dc:identifier><dc:title>{esc(options.title)}</dc:title><dc:language>zh-CN</dc:language><dc:creator>{esc(options.creator)}</dc:creator><meta property="dcterms:modified">{modified}</meta><meta name="cover" content="cover-image"/></metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="style" href="style.css" media-type="text/css"/><item id="cover-image" href="images/{cover_name}" media-type="{cover_mime}" properties="cover-image"/><item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="toc-page" href="chapter_00.xhtml" media-type="application/xhtml+xml"/>{manifest}</manifest><spine toc="ncx"><itemref idref="cover"/><itemref idref="toc-page"/>{spine}</spine><guide><reference type="cover" title="封面" href="cover.xhtml"/><reference type="text" title="目录" href="chapter_00.xhtml"/></guide></package>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN"><head><meta name="dtb:uid" content="urn:uuid:{book_id}"/><meta name="dtb:depth" content="1"/></head><docTitle><text>{esc(options.title)}</text></docTitle><navMap>{''.join(ncx_points)}</navMap></ncx>'''
    container = '<?xml version="1.0" encoding="utf-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'

    with zipfile.ZipFile(options.output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        archive.writestr("OEBPS/style.css", options.css_override if options.css_override is not None else extract_css(options.style))
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/cover.xhtml", xhtml("封面", cover_body, "cover-page"))
        archive.writestr("OEBPS/chapter_00.xhtml", xhtml("目录", toc_body, "chapter"))
        archive.writestr("OEBPS/nav.xhtml", xhtml("目录", nav_body))
        for filename, content in chapter_files:
            archive.writestr("OEBPS/" + filename, content)
        archive.write(options.cover, "OEBPS/images/" + cover_name)

    validate_epub(options.output, len(articles))
    return len(articles), options.output


def validate_epub(path: Path, article_count: int | None = None) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names[0] != "mimetype" or archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            raise ValueError("EPUB 的 mimetype 条目不符合规范")
        for name in names:
            if name.endswith((".xml", ".opf", ".ncx", ".xhtml")):
                ET.fromstring(archive.read(name))
        opf = archive.read("OEBPS/content.opf").decode("utf-8")
        if 'properties="cover-image"' not in opf:
            raise ValueError("封面声明缺失")
        if article_count is not None:
            ncx = ET.fromstring(archive.read("OEBPS/toc.ncx"))
            points = ncx.findall(".//{http://www.daisy.org/z3986/2005/ncx/}navPoint")
            if len(points) != article_count + 1:
                raise ValueError("目录条目数与文章数不一致")


def run_gui() -> None:
    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    root = tk.Tk()
    root.title("电子书制作工具")
    root.geometry("900x720")
    values = {name: tk.StringVar() for name in ("source", "cover", "style", "output", "title", "creator")}

    def choose_file(key: str, types):
        filename = filedialog.askopenfilename(filetypes=types)
        if filename:
            values[key].set(filename)
            if key == "source":
                source = Path(filename)
                values["title"].set(source.stem)
                values["output"].set(str(source.with_suffix(".epub")))
            elif key == "style":
                try:
                    css_editor.delete("1.0", "end")
                    css_editor.insert("1.0", extract_css(Path(filename)))
                    status.set("样式表已载入，可在下方直接修改。")
                except Exception as error:
                    messagebox.showerror("读取样式失败", str(error))

    fields = [
        ("Markdown 原稿", "source", lambda: choose_file("source", [("Markdown", "*.md")]), True),
        ("封面图片", "cover", lambda: choose_file("cover", [("图片", "*.jpg *.jpeg *.png")]), True),
        ("样式表", "style", lambda: choose_file("style", [("Markdown/CSS", "*.md *.css")]), True),
        ("输出 EPUB", "output", lambda: values["output"].set(filedialog.asksaveasfilename(defaultextension=".epub", filetypes=[("EPUB", "*.epub")])), True),
        ("电子书名称", "title", None, False),
        ("作者/机构", "creator", None, False),
    ]
    frame = ttk.Frame(root, padding=18)
    frame.pack(fill="both", expand=True)
    for row, (label, key, command, browse) in enumerate(fields):
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", pady=7)
        ttk.Entry(frame, textvariable=values[key], width=65).grid(row=row, column=1, sticky="ew", padx=10)
        if browse:
            ttk.Button(frame, text="选择…", command=command).grid(row=row, column=2)
    frame.columnconfigure(1, weight=1)
    values["creator"].set("我与我·深度写作营")
    style_bar = ttk.Frame(frame)
    style_bar.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(12, 4))
    ttk.Label(style_bar, text="样式编辑（CSS，可直接修改）").pack(side="left")

    def reload_style():
        path = Path(values["style"].get())
        try:
            css_editor.delete("1.0", "end")
            css_editor.insert("1.0", extract_css(path))
            status.set("已重新载入样式表。")
        except Exception as error:
            messagebox.showerror("读取样式失败", str(error))

    def save_style():
        path_text = values["style"].get().strip()
        if not path_text:
            messagebox.showwarning("尚未选择样式表", "请先选择样式表文件。")
            return
        try:
            Path(path_text).write_text(css_editor.get("1.0", "end-1c").rstrip() + "\n", encoding="utf-8")
            status.set("样式表已保存。")
        except Exception as error:
            messagebox.showerror("保存样式失败", str(error))

    ttk.Button(style_bar, text="重新载入", command=reload_style).pack(side="right", padx=(6, 0))
    ttk.Button(style_bar, text="保存样式表", command=save_style).pack(side="right")
    css_editor = scrolledtext.ScrolledText(frame, height=17, wrap="none", font=("Consolas", 10), undo=True)
    css_editor.grid(row=7, column=0, columnspan=3, sticky="nsew")
    frame.rowconfigure(7, weight=1)
    status = tk.StringVar(value="请选择原稿、封面和样式表。")
    ttk.Label(frame, textvariable=status).grid(row=8, column=0, columnspan=3, sticky="w", pady=(10, 5))

    def make_book():
        try:
            options = BookOptions(
                **{key: Path(values[key].get()) for key in ("source", "cover", "style", "output")},
                title=values["title"].get().strip(),
                creator=values["creator"].get().strip(),
                css_override=css_editor.get("1.0", "end-1c").rstrip() + "\n",
            )
            if not options.title or not options.creator:
                raise ValueError("请填写电子书名称和作者/机构。")
            status.set("正在生成并校验……")
            root.update_idletasks()
            count, output = build_epub(options)
            status.set(f"完成：{count} 篇文章，{output}")
            messagebox.showinfo("制作完成", f"已生成 {count} 篇文章：\n{output}")
        except Exception as error:
            status.set("生成失败，请检查提示。")
            messagebox.showerror("生成失败", str(error))

    ttk.Button(frame, text="生成电子书", command=make_book).grid(row=9, column=0, columnspan=3, pady=10)
    root.mainloop()


def main() -> None:
    parser = argparse.ArgumentParser(description="将按文章组织的 Markdown 文集制作成兼容性良好的 EPUB。")
    parser.add_argument("--gui", action="store_true", help="打开图形界面")
    parser.add_argument("--source", type=Path, help="Markdown 原稿")
    parser.add_argument("--cover", type=Path, help="JPG/PNG 封面")
    parser.add_argument("--style", type=Path, help="CSS 或包含 CSS 代码块的 Markdown")
    parser.add_argument("--output", type=Path, help="输出 EPUB")
    parser.add_argument("--title", help="电子书名称")
    parser.add_argument("--creator", default="我与我·深度写作营", help="作者或机构")
    args = parser.parse_args()
    if args.gui or not any((args.source, args.cover, args.style, args.output, args.title)):
        run_gui()
        return
    missing = [name for name in ("source", "cover", "style", "output", "title") if getattr(args, name) in (None, "")]
    if missing:
        parser.error("缺少参数：" + "、".join("--" + name for name in missing))
    count, output = build_epub(BookOptions(args.source, args.cover, args.style, args.output, args.title, args.creator))
    print(f"制作完成：{count} 篇文章；{output}")


if __name__ == "__main__":
    main()
