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
from urllib.parse import unquote
from xml.etree import ElementTree as ET


TITLE_RE = re.compile(r"^####\s+(?:标题[:：]\s*)?(.+?)\s*$")
HEADING_RE = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
AUTHOR_RE = re.compile(r"^作者\s*[:：]?\s*(.*)$")
INLINE_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
IMAGE_RE = re.compile(r'!\[([^\]\n]*)\]\(\s*(?:<([^>\n]+)>|((?:[^()\n]|\([^()\n]*\))*?))\s*\)')


class ImageAssets:
    """Resolve local Markdown images and assign portable EPUB paths."""

    def __init__(self, source: Path):
        self.base = source.resolve().parent
        self.files = {}

    def render(self, match) -> str:
        alt, angled, destination = match.groups()
        target = angled if angled is not None else destination.strip()
        title = ""
        if angled is None:
            titled = re.fullmatch(r'''(.*?)\s+["'](.*)["']''', target)
            if titled:
                target, title = titled.groups()
        target = unquote(html.unescape(target))
        if re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target) and not re.match(r"^[a-zA-Z]:[\\/]", target):
            raise ValueError(f"正文图片需使用本地文件，请先下载图片并修改路径：{target}")
        path = Path(target.replace("\\", "/"))
        if not path.is_absolute():
            path = self.base / path
        path = path.resolve()
        if not target or not path.is_file():
            raise FileNotFoundError(f"正文图片不存在：{target}（查找位置：{path}）")
        if path not in self.files:
            data = path.read_bytes()
            if data.startswith(b"\x89PNG\r\n\x1a\n"):
                suffix, mime = "png", "image/png"
            elif data.startswith(b"\xff\xd8"):
                suffix, mime = "jpg", "image/jpeg"
            elif data.startswith((b"GIF87a", b"GIF89a")):
                suffix, mime = "gif", "image/gif"
            else:
                raise ValueError(f"正文图片格式不支持：{path}；请使用 JPG、PNG 或 GIF")
            self.files[path] = (f"images/body_{len(self.files) + 1:04d}.{suffix}", mime)
        href, _ = self.files[path]
        attrs = f' src="{href}" alt="{html.escape(alt, quote=True)}"'
        if title:
            attrs += f' title="{html.escape(title, quote=True)}"'
        return f'<img{attrs}/>'

    def inline(self, text: str) -> str:
        result = []
        start = 0
        for match in IMAGE_RE.finditer(text):
            result.append(inline(text[start:match.start()]))
            result.append(self.render(match))
            start = match.end()
        result.append(inline(text[start:]))
        return "".join(result)


@dataclass
class Article:
    title: str
    author: str
    paragraphs: list[str]
    level: int = 4


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


def normalize_heading(title: str, level: int) -> str:
    title = title.strip()
    if level == 4:
        return re.sub(r"^标题[:：]\s*", "", title).strip()
    if level == 1:
        preface = re.fullmatch(r"(前言|前沿|自序|序言)(?:[\s:：、—–-]+(.*))?", title)
        if preface:
            keyword, subtitle = preface.groups()
            subtitle = (subtitle or "").strip()
            return subtitle or ("前言" if keyword == "前沿" else keyword)
    return title


def parse_articles(source: Path) -> list[Article]:
    lines = source.read_text(encoding="utf-8-sig").splitlines()
    headings = []
    fence = None
    for i, line in enumerate(lines):
        stripped = line.strip()
        marker = re.match(r"^(`{3,}|~{3,})", stripped)
        if marker:
            token = marker.group(1)
            if fence is None:
                fence = token
            elif token[0] == fence[0] and len(token) >= len(fence) and not stripped[len(token):].strip():
                fence = None
            continue
        match = HEADING_RE.match(stripped) if fence is None else None
        if match:
            level = len(match.group(1))
            title = normalize_heading(match.group(2), level)
            headings.append((i, level, title))
    articles: list[Article] = []
    for number, (start, level, title) in enumerate(headings):
        end = headings[number + 1][0] if number + 1 < len(headings) else len(lines)
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
        articles.append(Article(title.strip(), author, paragraphs, level))
    return articles


def inline(text: str) -> str:
    parts = INLINE_BOLD_RE.split(text)
    return "".join(
        "<strong>%s</strong>" % esc(part) if index % 2 else esc(part)
        for index, part in enumerate(parts)
    )


def render_paragraphs(paragraphs: Iterable[str], images: ImageAssets | None = None) -> str:
    result = []
    render_inline = images.inline if images is not None else inline
    fence = None
    code = []
    for paragraph in paragraphs:
        marker = re.match(r"^(`{3,}|~{3,})", paragraph)
        if fence is not None:
            if marker and marker.group(1)[0] == fence[0] and len(marker.group(1)) >= len(fence) and not paragraph[len(marker.group(1)):].strip():
                result.append('<pre><code>' + esc('\n'.join(code)) + '</code></pre>')
                fence, code = None, []
            else:
                code.append(paragraph)
            continue
        if marker:
            fence = marker.group(1)
            continue
        heading = re.fullmatch(r"(#{5,6})\s+(.+?)\s*#*\s*", paragraph)
        if heading:
            tag = "h2" if len(heading.group(1)) == 5 else "h3"
            result.append(f'<{tag} class="section-title">{render_inline(heading.group(2))}</{tag}>')
            continue
        bold_line = re.fullmatch(r"\*\*(.+)\*\*", paragraph)
        if bold_line:
            result.append('<p class="section-title"><strong>%s</strong></p>' % render_inline(bold_line.group(1)))
        else:
            css_class = ' class="image-container"' if IMAGE_RE.fullmatch(paragraph) else ""
            result.append(f"<p{css_class}>{render_inline(paragraph)}</p>")
    if fence is not None:
        result.append('<pre><code>' + esc('\n'.join(code)) + '</code></pre>')
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
    empty_articles = [a.title for a in articles if a.level == 4 and not a.paragraphs]
    if empty_articles:
        raise ValueError("以下文章正文为空：" + "、".join(empty_articles))

    cover_name, cover_mime, cover_width, cover_height = image_info(options.cover)
    book_id = str(uuid.uuid4())
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    options.output.parent.mkdir(parents=True, exist_ok=True)

    chapter_items = []
    spine_items = []
    toc_root = ET.Element("ol", {"class": "toc-tree"})
    nav_root = ET.Element("ol")
    ET.SubElement(ET.SubElement(nav_root, "li"), "a", {"href": "chapter_00.xhtml"}).text = "目录"
    ncx_root = ET.Element("navMap")
    toc_point = ET.SubElement(ncx_root, "navPoint", {"id": "np0", "playOrder": "1"})
    ET.SubElement(ET.SubElement(toc_point, "navLabel"), "text").text = "目录"
    ET.SubElement(toc_point, "content", {"src": "chapter_00.xhtml"})
    hierarchy = []
    max_depth = 1
    chapter_files: list[tuple[str, str]] = []
    images = ImageAssets(options.source)

    for index, article in enumerate(articles, 1):
        filename = f"chapter_{index:02d}.xhtml"
        chapter_items.append(f'<item id="ch{index:02d}" href="{filename}" media-type="application/xhtml+xml"/>')
        spine_items.append(f'<itemref idref="ch{index:02d}"/>')
        label = f"{article.title}  /{article.author}" if article.author else article.title
        while hierarchy and hierarchy[-1][0] >= article.level:
            hierarchy.pop()
        toc_parent, nav_parent, ncx_parent = toc_root, nav_root, ncx_root
        if hierarchy:
            _, toc_item, nav_item, ncx_parent = hierarchy[-1]
            toc_parent = toc_item.find("ol")
            if toc_parent is None:
                toc_parent = ET.SubElement(toc_item, "ol")
            nav_parent = nav_item.find("ol")
            if nav_parent is None:
                nav_parent = ET.SubElement(nav_item, "ol")
        toc_item = ET.SubElement(toc_parent, "li", {"class": f"toc-item toc-level-{article.level}"})
        ET.SubElement(toc_item, "a", {"class": "toc-title", "href": filename}).text = label
        nav_item = ET.SubElement(nav_parent, "li")
        ET.SubElement(nav_item, "a", {"href": filename}).text = label
        point = ET.SubElement(ncx_parent, "navPoint", {"id": f"np{index}", "playOrder": str(index + 1)})
        ET.SubElement(ET.SubElement(point, "navLabel"), "text").text = label
        ET.SubElement(point, "content", {"src": filename})
        # 一级标题用于书名、前言等独立页面，后续卷不应归入前言。
        if article.level != 1:
            hierarchy.append((article.level, toc_item, nav_item, point))
        max_depth = max(max_depth, len(hierarchy))
        author_html = f'<p class="author">{esc(article.author)}</p>' if article.author else ""
        body = f'<section class="chapter"><h1 class="chapter-title">{esc(article.title)}</h1>{author_html}<div class="content">{render_paragraphs(article.paragraphs, images)}</div></section>'
        chapter_files.append((filename, xhtml(article.title, body)))

    toc_body = '<section class="chapter toc"><h1 class="chapter-title toc-header">目录</h1><div class="toc-list">' + ET.tostring(toc_root, encoding="unicode") + "</div></section>"
    nav_body = '<nav epub:type="toc" id="toc"><h1>目录</h1>' + ET.tostring(nav_root, encoding="unicode") + '</nav>'
    cover_body = f'<div class="cover"><svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 {cover_width} {cover_height}" preserveAspectRatio="xMidYMid meet"><image width="{cover_width}" height="{cover_height}" xlink:href="images/{cover_name}"/></svg></div>'

    manifest = "".join(chapter_items)
    manifest += "".join(f'<item id="body-image-{i}" href="{href}" media-type="{mime}"/>'
                        for i, (href, mime) in enumerate(images.files.values(), 1))
    spine = "".join(spine_items)
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="BookID" xml:lang="zh-CN"><metadata xmlns:dc="http://purl.org/dc/elements/1.1/"><dc:identifier id="BookID">urn:uuid:{book_id}</dc:identifier><dc:title>{esc(options.title)}</dc:title><dc:language>zh-CN</dc:language><dc:creator>{esc(options.creator)}</dc:creator><meta property="dcterms:modified">{modified}</meta><meta name="cover" content="cover-image"/></metadata><manifest><item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/><item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/><item id="style" href="style.css" media-type="text/css"/><item id="cover-image" href="images/{cover_name}" media-type="{cover_mime}" properties="cover-image"/><item id="cover" href="cover.xhtml" media-type="application/xhtml+xml"/><item id="toc-page" href="chapter_00.xhtml" media-type="application/xhtml+xml"/>{manifest}</manifest><spine toc="ncx"><itemref idref="cover"/><itemref idref="toc-page"/>{spine}</spine><guide><reference type="cover" title="封面" href="cover.xhtml"/><reference type="text" title="目录" href="chapter_00.xhtml"/></guide></package>'''
    ncx = f'''<?xml version="1.0" encoding="utf-8"?><ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1" xml:lang="zh-CN"><head><meta name="dtb:uid" content="urn:uuid:{book_id}"/><meta name="dtb:depth" content="{max_depth}"/></head><docTitle><text>{esc(options.title)}</text></docTitle>{ET.tostring(ncx_root, encoding="unicode")}</ncx>'''
    container = '<?xml version="1.0" encoding="utf-8"?><container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles></container>'

    with zipfile.ZipFile(options.output, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        archive.writestr("META-INF/container.xml", container)
        archive.writestr("OEBPS/content.opf", opf)
        css = options.css_override if options.css_override is not None else extract_css(options.style)
        css += '\n.toc-tree, .toc-tree ol { list-style: none; padding-left: 1.5em; }\n.toc-tree { padding-left: 0; }\n.toc-tree li { display: list-item; text-indent: 0; }\n'
        css += '\n.content img { max-width: 100%; height: auto; }\n.content .image-container { text-align: center; text-indent: 0; }\n'
        archive.writestr("OEBPS/style.css", css)
        archive.writestr("OEBPS/toc.ncx", ncx)
        archive.writestr("OEBPS/cover.xhtml", xhtml("封面", cover_body, "cover-page"))
        archive.writestr("OEBPS/chapter_00.xhtml", xhtml("目录", toc_body, "chapter"))
        archive.writestr("OEBPS/nav.xhtml", xhtml("目录", nav_body))
        for filename, content in chapter_files:
            archive.writestr("OEBPS/" + filename, content)
        archive.write(options.cover, "OEBPS/images/" + cover_name)
        for path, (href, _) in images.files.items():
            archive.write(path, "OEBPS/" + href)

    validate_epub(options.output, len(articles))
    return sum(a.level == 4 for a in articles) or len(articles), options.output


def validate_epub(path: Path, article_count: int | None = None) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if names[0] != "mimetype" or archive.getinfo("mimetype").compress_type != zipfile.ZIP_STORED:
            raise ValueError("EPUB 的 mimetype 条目不符合规范")
        for name in names:
            if name.endswith((".xml", ".opf", ".ncx", ".xhtml")):
                tree = ET.fromstring(archive.read(name))
                if name.endswith(".xhtml"):
                    for image in tree.findall(".//{http://www.w3.org/1999/xhtml}img"):
                        href = image.get("src", "")
                        if not href or "OEBPS/" + href not in names:
                            raise ValueError(f"正文图片资源缺失：{name} -> {href}")
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

    default_style = Path(__file__).resolve().with_name("EPUB样式表.md")
    try:
        default_css = extract_css(default_style)
    except (OSError, UnicodeError):
        status.set("默认样式表不可用，请手动选择样式表。")
    else:
        values["style"].set(str(default_style))
        css_editor.insert("1.0", default_css)
        status.set("已载入默认样式表，请选择原稿和封面。")

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
