import base64
import tempfile
import unittest
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from epub_maker import BookOptions, build_epub, parse_articles


class DirectoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'source.md'
        self.cover = self.root / 'cover.png'
        self.cover.write_bytes(base64.b64decode(
            'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII='))
        self.style = self.root / 'style.css'
        self.style.write_text('p { line-height: 1.8; }', encoding='utf-8')

    def build(self, text):
        self.source.write_text(text, encoding='utf-8')
        return build_epub(BookOptions(self.source, self.cover, self.style,
                                     self.root / 'book.epub', 'Book', 'Creator'))

    def test_hierarchy_and_content(self):
        count, output = self.build(
            '# Book\nSubtitle\n# Preface\nIntroduction\n'
            '## Volume A\n### Chapter A\nChapter introduction\n'
            '#### Article A & B\nBody A\n##### Subheading\nDetails\n'
            '#### Article B\nBody B\n## Volume B\n### Chapter B\n'
            '#### Article C\nBody C\n')
        self.assertEqual(count, 3)
        h = '{http://www.w3.org/1999/xhtml}'
        n = '{http://www.daisy.org/z3986/2005/ncx/}'
        with zipfile.ZipFile(output) as archive:
            for filename in ('chapter_00.xhtml', 'nav.xhtml'):
                tree = ET.fromstring(archive.read('OEBPS/' + filename))
                root_list = tree.find('.//' + h + 'ol')
                volumes = root_list.findall(h + 'li')
                if filename == 'nav.xhtml':
                    volumes = volumes[1:]
                self.assertEqual([v.find(h + 'a').text for v in volumes],
                                 ['Book', 'Preface', 'Volume A', 'Volume B'])
                chapter = volumes[2].find(h + 'ol/' + h + 'li')
                self.assertEqual(chapter.find(h + 'a').text, 'Chapter A')
                self.assertEqual([a.text for a in chapter.findall(h + 'ol/' + h + 'li/' + h + 'a')],
                                 ['Article A & B', 'Article B'])
            ncx = ET.fromstring(archive.read('OEBPS/toc.ncx'))
            points = ncx.findall('.//' + n + 'navPoint')
            self.assertEqual(len(points), 10)
            self.assertEqual(ncx.find('.//' + n + 'meta[@name="dtb:depth"]').get('content'), '3')
            texts = '\n'.join(archive.read(name).decode('utf-8') for name in archive.namelist()
                              if name.startswith('OEBPS/chapter_'))
            self.assertIn('Introduction', texts)
            self.assertIn('Chapter introduction', texts)
            self.assertIn('<h2 class="section-title">Subheading</h2>', texts)
            self.assertNotIn('class="author"', texts)

    def test_flat_author_and_empty_body(self):
        count, output = self.build('#### A\n**\u4f5c\u8005\uff1aAlice**\nBody\n#### B\nBody B\n')
        self.assertEqual(count, 2)
        with zipfile.ZipFile(output) as archive:
            self.assertIn('A  /Alice', archive.read('OEBPS/nav.xhtml').decode('utf-8'))
        with self.assertRaises(ValueError):
            self.build('## Volume\n### Chapter\n#### Empty\n')

    def test_preface_titles_in_all_outputs(self):
        cases = [
            ('# 前沿', '前言'), ('#  前言', '前言'),
            (' # 自序', '自序'), ('# 序言', '序言'),
            ('# 前言：我想长出一双新眼睛', '我想长出一双新眼睛'),
            ('# 前沿: 新的开始', '新的开始'),
            ('# 自序  写在出发之前', '写在出发之前'),
            ('# 序言——一路向前', '一路向前'),
            ('# 前言：', '前言'), ('# 前沿  ', '前言'),
            ('# 前沿科技', '前沿科技'), ('# 普通书名', '普通书名'),
            ('#### 前言：文章名称', '前言：文章名称'),
        ]
        for heading, expected in cases:
            with self.subTest(heading=heading):
                _, output = self.build(heading + '\nPreface body\n')
                self.assertEqual(parse_articles(self.source)[0].title, expected)
                with zipfile.ZipFile(output) as archive:
                    h = '{http://www.w3.org/1999/xhtml}'
                    chapter = ET.fromstring(archive.read('OEBPS/chapter_01.xhtml'))
                    self.assertEqual(chapter.find('.//' + h + 'h1').text, expected)
                    self.assertEqual(chapter.find('.//' + h + 'title').text, expected)
                    self.assertIn('Preface body', ''.join(chapter.itertext()))
                    for name in ('chapter_00.xhtml', 'nav.xhtml'):
                        tree = ET.fromstring(archive.read('OEBPS/' + name))
                        link = tree.find('.//' + h + 'a[@href="chapter_01.xhtml"]')
                        self.assertEqual(link.text, expected)
                    n = '{http://www.daisy.org/z3986/2005/ncx/}'
                    ncx = ET.fromstring(archive.read('OEBPS/toc.ncx'))
                    self.assertEqual(ncx.find('.//' + n + 'navPoint[@id="np1"]/' + n + 'navLabel/' + n + 'text').text, expected)

    def test_skipped_levels_and_fences(self):
        self.source.write_text('## Volume\n#### Article\nBody\n```markdown\n## Example\n```\n', encoding='utf-8')
        nodes = parse_articles(self.source)
        self.assertEqual([(a.title, a.level) for a in nodes], [('Volume', 2), ('Article', 4)])

    def test_optional_volume_and_chapter(self):
        cases = [
            ('## V\n### C\n#### A\nBody\n', [('V',), ('V', 'C'), ('V', 'C', 'A')]),
            ('### C\n#### A\nBody\n', [('C',), ('C', 'A')]),
            ('## V\n#### A\nBody\n', [('V',), ('V', 'A')]),
            ('#### A\nBody\n#### B\nBody\n', [('A',), ('B',)]),
            ('#### A\nBody\n### C\n#### B\nBody\n## V\n#### D\nBody\n',
             [('A',), ('C',), ('C', 'B'), ('V',), ('V', 'D')]),
        ]

        def paths(parent, ns, item_tag, label_path, prefix=()):
            result = []
            for item in parent.findall(ns + item_tag):
                label = item.find(label_path).text
                if label == '\u76ee\u5f55':
                    continue
                current = prefix + (label,)
                result.append(current)
                nested = item if item_tag == 'navPoint' else item.find(ns + 'ol')
                if nested is not None:
                    result.extend(paths(nested, ns, item_tag, label_path, current))
            return result

        for text, expected in cases:
            with self.subTest(structure=expected):
                _, output = self.build(text)
                with zipfile.ZipFile(output) as archive:
                    h = '{http://www.w3.org/1999/xhtml}'
                    for name in ('chapter_00.xhtml', 'nav.xhtml'):
                        tree = ET.fromstring(archive.read('OEBPS/' + name))
                        self.assertEqual(paths(tree.find('.//' + h + 'ol'), h, 'li', h + 'a'), expected)
                    n = '{http://www.daisy.org/z3986/2005/ncx/}'
                    tree = ET.fromstring(archive.read('OEBPS/toc.ncx'))
                    self.assertEqual(paths(tree.find(n + 'navMap'), n, 'navPoint', n + 'navLabel/' + n + 'text'), expected)
                    depth = tree.find('.//' + n + 'meta[@name="dtb:depth"]').get('content')
                    self.assertEqual(int(depth), max(map(len, expected)))

    def test_images_and_portable_paths(self):
        folder = self.root / '\u56fe'
        folder.mkdir()
        image = folder / 'a (1).png'
        image.write_bytes(self.cover.read_bytes())
        count, output = self.build(
            '#### Images\n![A & "B"](<\u56fe/a (1).png>)\n'
            'Text ![Inline](\u56fe/a%20%281%29.png "Caption") after\n'
            '![](\u56fe/a (1).png)\n'
            '```markdown\n![](missing-example.png)\n```\n')
        with zipfile.ZipFile(output) as archive:
            body_images = [n for n in archive.namelist() if '/body_' in n]
            self.assertEqual(len(body_images), 1)
            self.assertEqual(archive.read(body_images[0]), image.read_bytes())
            tree = ET.fromstring(archive.read('OEBPS/chapter_01.xhtml'))
            images = tree.findall('.//{http://www.w3.org/1999/xhtml}img')
            self.assertEqual(len(images), 3)
            self.assertEqual(images[0].get('alt'), 'A & "B"')
            self.assertEqual(images[1].get('title'), 'Caption')
            self.assertEqual(len(set(img.get('src') for img in images)), 1)
            opf = ET.fromstring(archive.read('OEBPS/content.opf'))
            item = opf.find('.//{http://www.idpf.org/2007/opf}item[@id="body-image-1"]')
            self.assertEqual(item.get('media-type'), 'image/png')

    def test_missing_and_remote_images(self):
        with self.assertRaises(FileNotFoundError):
            self.build('#### Article\n![](missing.png)\n')
        with self.assertRaises(ValueError):
            self.build('#### Article\n![](https://example.com/a.png)\n')


if __name__ == '__main__':
    unittest.main()
