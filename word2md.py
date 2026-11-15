# -*- coding: utf-8 -*-
"""把 Word(.docx) 转成 .md，供当前项目的 EPUB 工具使用。

用法：python word2md.py "学员.docx" [输出.md]

转换规则：
- 按 Word 标题样式识别结构：标题3→### 章节、标题4→#### 文章、标题5→##### 小节
- 「自序/前言/序」这类标题4 → # 前言
- 正文加粗片段 → **文字**，保留直接加粗及字符/段落样式继承的加粗
- 正文斜体片段 → *文字*
- 图片提取到输出 Markdown 旁的「文件名_图片/」子文件夹
- 图注：支持跳过图片后的空段落、同段说明及“图片说明：/图注：/图1：”等写法；下一段若为「图：xxx」「图1-1：xxx」或 Word 题注样式，自动取为图注
  （带编号的「图1-1：xxx」整行保留，含编号）
- 兜底（样式丢失时）：加粗独立短段→文章标题；「数字.」开头独立短段→小节标题；
  这些靠猜的行会列在最后的「需人工核对」清单里
- 可选开关（默认关闭）：整段被【】包起来的行→去掉【】。既处理普通正文段（转成 ##### 小标题），
  也处理带标题样式的段（如「标题5」的【xxx】→ ##### xxx，只去括号、层级不变）。
  打开方式：把 BRACKET_AS_SUBTITLE 改成 True，或命令行加 --bracket-subtitle
"""
import io, os, re, sys
from docx import Document
from docx.oxml.ns import qn
from docx.text.run import Run
from docx.text.paragraph import Paragraph

IMG_EXT = {"jpeg": "jpg", "jpe": "jpg"}

# 开关：把「整段被【】包起来」的行识别成正文小标题（输出 #####，去掉【】）。默认关闭。
# 打开方式：改成 True，或运行时加命令行参数 --bracket-subtitle。
BRACKET_AS_SUBTITLE = False
BRACKET_SUB_RE = re.compile(r"^【\s*(.+?)\s*】$")   # 整行形如【xxx】才认；xxx 作为小标题文字

# 图注识别（图片的下一段）：
#   带编号：图1-1：xxx / 图 1-1 xxx / 图2-6：xxx …… → 整行作为图注（保留编号）
#   旧写法：图：xxx → 去掉「图：」前缀，取后面的说明
#   另外，Word「题注」样式(Caption)的段落也直接当图注
CAP_NUM_RE = re.compile(r"^图\s*\d+(?:\s*[-－—–.]\s*\d+)*(?:\s*[:：、.．)）]\s*|\s+|(?=[^\d\s])).+$")
CAP_PLAIN_RE = re.compile(r"^(?:图片说明|图注说明|图片名|图片|图注|图说|照片说明|说明|图)\s*[:：]\s*(.+)$")
CAP_STYLE_RE = re.compile(r"题注|caption", re.I)


def caption_text(para):
    # Text in Word textboxes may be absent from Paragraph.text.
    text = para.text.strip()
    if not text:
        text = "".join(node.text or "" for node in para._p.iter(qn("w:t"))).strip()
    return text.replace("\u2060", "").replace("\ufeff", "")


def paragraph_style_name(para):
    """Some documents lack a default style or reference an undefined style."""
    return getattr(para.style, "name", "") or ""


def find_caption(paras, index):
    """Skip empty paragraphs, never cross an intervening table, heading or image."""
    previous = paras[index]
    for j in range(index+1, len(paras)):
        candidate = paras[j]
        if previous._p.getnext() is not candidate._p:
            return None, index
        if candidate._p.find(".//"+qn("a:blip")) is not None:
            return None, index
        if heading_level(candidate):
            return None, index
        text = caption_text(candidate)
        if text:
            return match_caption(candidate), j
        previous = candidate
    return None, index


def match_caption(para):
    """这一段是不是图注？是则返回图注文字，否则返回 None。"""
    t = caption_text(para)
    if not t:
        return None
    if CAP_STYLE_RE.search(paragraph_style_name(para)):
        return t
    if CAP_NUM_RE.match(t):          # 图1-1：xxx —— 整行都要（编号是图注的一部分）
        return t
    m = CAP_PLAIN_RE.match(t)        # 图：xxx —— 只取说明部分（保持旧行为）
    if m:
        return m.group(1).strip()
    return None


def heading_level(style_name):
    """按样式名返回标题层级 1~6，非标题（正文）返回 0。"""
    if not isinstance(style_name, str):
        para = style_name
        outline = para._p.xpath('./w:pPr/w:outlineLvl')
        if outline and int(outline[0].get(qn('w:val'))) < 6:
            return int(outline[0].get(qn('w:val'))) + 1
        style, seen = para.style, set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            level = heading_level(style.name or '')
            if level:
                return level
            outline = style.element.xpath('./w:pPr/w:outlineLvl')
            if outline and int(outline[0].get(qn('w:val'))) < 6:
                return int(outline[0].get(qn('w:val'))) + 1
            style = style.base_style
        return 0
    if re.fullmatch(r"title|标题", style_name, re.I):
        return 1
    m = re.search(r"(?:heading|标题)\s*([1-6])", style_name, re.I)
    return int(m.group(1)) if m else 0


def clean_title(s):
    """去掉学员加的「标题1:」「标题：」这类占位前缀。"""
    return re.sub(r"^标题[0-9\s]*[:：]\s*", "", s).strip()


def _empty(paras, j):
    return j < 0 or j >= len(paras) or not paras[j].text.strip()


def paragraph_runs(para):
    """Include hyperlink text in document order, as well as ordinary runs."""
    return [Run(node, para) for node in para._p.xpath("./w:r | ./w:hyperlink/w:r")]


def effective_bold(run, para, property_name="bold"):
    """Resolve direct formatting and Word's toggle properties in style chains."""
    direct = getattr(run, property_name)
    if direct is not None:
        return direct  # Explicit False must override inherited formatting too.
    defaults = para.part.document.styles.element.xpath(
        "./w:docDefaults/w:rPrDefault/w:rPr/w:" + ("b" if property_name == "bold" else "i")
    )
    bold = bool(defaults and defaults[0].val)
    for style in (para.style, run.style):
        chain, seen = [], set()
        while style is not None and style.style_id not in seen:
            seen.add(style.style_id)
            chain.append(style)
            style = style.base_style
        for ancestor in reversed(chain):
            if getattr(ancestor.font, property_name):
                bold = not bold
    return bold


def paragraph_markdown(para):
    """Merge adjacent equally formatted runs before adding Markdown markers."""
    groups = []
    for run in paragraph_runs(para):
        if not run.text:
            continue
        formatting = (effective_bold(run, para), effective_bold(run, para, "italic"))
        if groups and groups[-1][0] == formatting:
            groups[-1][1] += run.text
        else:
            groups.append([formatting, run.text])
    parts = []
    for (bold, italic), text in groups:
        marker = ('**' if bold else '') + ('*' if italic else '')
        if marker:
            text = "\n".join(
                re.sub(r"^(\s*)(\S(?:.*\S)?)(\s*)$",
                       lambda m: m[1] + marker + m[2] + marker + m[3], line)
                for line in text.split("\n")
            )
        parts.append(text)
    return "".join(parts).strip()


def extracted_image(blip, blob, ext):
    """Bake EXIF orientation and this picture's Word rotation into PNG pixels."""
    picture = blip
    while picture is not None and picture.tag != qn('pic:pic'):
        picture = picture.getparent()
    transform = picture.find('./' + qn('pic:spPr') + '/' + qn('a:xfrm')) if picture is not None else None
    rotation = float(transform.get('rot', '0')) / 60000 if transform is not None else 0
    flip_h = transform is not None and transform.get('flipH', '').lower() in ('1', 'true', 'on')
    flip_v = transform is not None and transform.get('flipV', '').lower() in ('1', 'true', 'on')
    from PIL import Image, ImageOps
    # Leave unmodified images byte-for-byte intact, including vector formats.
    try:
        with Image.open(io.BytesIO(blob)) as source:
            orientation = source.getexif().get(274, 1)
            if not (rotation % 360 or flip_h or flip_v or orientation not in (1, None)):
                return blob, ext
            image = ImageOps.exif_transpose(source).convert('RGBA')
            if flip_h:
                image = ImageOps.mirror(image)
            if flip_v:
                image = ImageOps.flip(image)
            if rotation % 360:
                # DrawingML uses clockwise angles; Pillow uses counterclockwise.
                image = image.rotate(-rotation, expand=True)
            output = io.BytesIO()
            image.save(output, format='PNG')
            return output.getvalue(), 'png'
    except Exception as error:
        if rotation % 360 or flip_h or flip_v:
            raise RuntimeError('无法保留 Word 图片的旋转方向，请将该图片另存为 PNG 后重新插入。' + str(error)) from None
        return blob, ext


def convert(src, out=None, bracket_subtitle=None, report=None):
    if bracket_subtitle is None:
        bracket_subtitle = BRACKET_AS_SUBTITLE
    src = os.path.abspath(src)
    if not src.lower().endswith('.docx'):
        raise ValueError("请选择 .docx 文件；旧版 .doc 请先用 Word 另存为 .docx。")
    doc = Document(src)
    if out is None:
        out = os.path.splitext(src)[0] + ".md"
    out = os.path.abspath(out)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    image_folder = os.path.splitext(os.path.basename(out))[0] + "_图片"
    img_dir = os.path.join(os.path.dirname(out), image_folder)

    # Keep table text too; EPUB currently displays it as consecutive paragraphs.
    paras = [Paragraph(node, doc._body) for node in doc.element.body.iter(qn('w:p'))
             if not any(parent.tag == qn('w:p') for parent in node.iterancestors())]
    n = len(paras)
    lines, suspicious = [], []
    if doc.tables:
        suspicious.append(("表格", "表格内容按段落展开，请核对顺序和排版"))
    if doc.element.body.xpath('.//w:footnoteReference | .//w:endnoteReference'):
        suspicious.append(("注释", "脚注、尾注暂未导入，请改为正文"))
    if doc.element.body.xpath('.//w:txbxContent'):
        suspicious.append(("文本框", "请核对文本框内容，建议改为普通正文段落"))
    img_no = 0
    i = 0
    while i < n:
        p = paras[i]
        text = p.text.strip()

        # 图片段落：提取到 图/imgN.png，生成 ![](图/imgN.png)；下一段是图注（图：xxx / 图1-1：xxx / 题注样式）
        blips = p._p.findall(".//" + qn("a:blip"))
        if blips:
            inline_caption = match_caption(p) if text else None
            caption = inline_caption
            if caption is None:
                caption, caption_index = find_caption(paras, i)
                if caption is not None:
                    i = caption_index
            if text and inline_caption is None:
                lines.append(paragraph_markdown(p))
        for blip in blips:
            rid = blip.get(qn("r:embed"))
            part = doc.part.related_parts.get(rid)
            if part is not None:
                ext = part.content_type.split("/")[-1].lower()
                ext = IMG_EXT.get(ext, ext)
                blob, ext = extracted_image(blip, part.blob, ext)
                img_no += 1
                fn = f"img{img_no}.{ext}"
                os.makedirs(img_dir, exist_ok=True)
                with open(os.path.join(img_dir, fn), "wb") as f:
                    f.write(blob)
                alt = (caption or '').replace('[', '（').replace(']', '）')
                lines.append(f"![{alt}](<{image_folder}/{fn}>)")
            else:
                raise ValueError("Word 中存在无法读取的图片（可能是外部链接），请将图片嵌入文档后重试。")
        if blips:
            i += 1
            continue

        if not text:
            i += 1
            continue

        # 有标题样式的：直接映射
        level = heading_level(p)
        if level:
            t = clean_title(text)
            if bracket_subtitle:            # 开关打开：标题若整段被【】包起来，去掉【】（如「标题5」样式的小节）
                bm = BRACKET_SUB_RE.match(t)
                if bm:
                    t = bm.group(1).strip()
            if level == 4 and re.match(r"^(自序|前言|前沿|序言|代序|写在前面)(?:$|[\s:：、—–-])", t):
                if t.startswith("前沿"):
                    t = "前言" + t[len("前沿"):]
                lines.append(f"# {t}")
            else:
                lines.append(f"{'#' * level} {t}")
            i += 1
            continue

        # 开关打开时：整段被【】包起来 → 正文小标题（#####），去掉【】
        if bracket_subtitle:
            bm = BRACKET_SUB_RE.match(text)
            if bm:
                lines.append(f"##### {bm.group(1).strip()}")
                i += 1
                continue

        # 兜底：样式丢失，靠「加粗独立短段」猜文章标题、「数字.开头独立短段」猜小节标题
        visible_runs = [r for r in paragraph_runs(p) if r.text.strip()]
        bold_all = bool(visible_runs) and all(effective_bold(r, p) for r in visible_runs)
        if not text.startswith("作者") and bold_all and len(text) <= 30 and _empty(paras, i - 1) and _empty(paras, i + 1):
            lines.append(f"#### {clean_title(text)}")
            suspicious.append(("文章标题(猜)", clean_title(text)))
            i += 1
            continue
        if re.match(r"^\d{1,2}\.\s", text) and len(text) <= 30 and _empty(paras, i - 1):
            lines.append(f"##### {text}")
            suspicious.append(("小节标题(猜)", text))
            i += 1
            continue

        lines.append(paragraph_markdown(p))
        i += 1

    if not any(re.match(r"^#{1,4}\s", line) for line in lines):
        lines.insert(0, "# " + os.path.splitext(os.path.basename(src))[0])
        suspicious.append(("标题", "未识别到标题，以文件名作为标题，请核对原稿标题样式"))
    elif lines and not re.match(r"^#{1,4}\s", lines[0]):
        lines.insert(0, "# " + os.path.splitext(os.path.basename(src))[0])
        suspicious.append(("开篇正文", "首个标题前的正文已保存在文件名标题页面，请核对"))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n\n".join(lines) + "\n")

    print(f"已生成 {out}")
    print(f"提取图片 {img_no} 张 → {img_dir}")
    if suspicious:
        print(f"\n以下 {len(suspicious)} 处转换结果需人工核对：")
        for kind, t in suspicious:
            print(f"  [{kind}] {t}")
    if report is not None:
        report.extend(f"{kind}：{t}" for kind, t in suspicious)
    return out, img_no


if __name__ == "__main__":
    if (getattr(sys.stdout, "encoding", "") or "").lower() != "utf-8" and hasattr(sys.stdout, "buffer"):
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
    args = sys.argv[1:]
    bracket = False
    if "--bracket-subtitle" in args:
        bracket = True
        args.remove("--bracket-subtitle")
    if not args:
        print("用法：python word2md.py \"学员.docx\" [输出.md] [--bracket-subtitle]")
        print("  --bracket-subtitle  把整段被【】包起来的行当正文小标题（#####，去掉【】）；默认关闭")
        sys.exit(1)
    convert(args[0], args[1] if len(args) > 1 else None, bracket_subtitle=bracket or None)
