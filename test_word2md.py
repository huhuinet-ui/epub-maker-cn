"""Word conversion integration tests; requires requirements-word.txt."""
import hashlib
import tempfile
import unittest
import zipfile
from pathlib import Path

from epub_maker import BookOptions, build_epub, parse_articles
try:
    from docx import Document
    from PIL import Image
    from word2md import convert
except ImportError:
    Document = None


@unittest.skipIf(Document is None, 'Install requirements-word.txt for Word integration tests')
class WordImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / 'manuscript.docx'
        self.image = self.root / 'picture.png'
        Image.new('RGB', (8, 4), 'red').save(str(self.image))
        doc = Document()
        doc.add_heading('前沿：新的开始', level=4)
        doc.add_paragraph('Preface body')
        doc.add_heading('Volume', level=2)
        doc.add_heading('Chapter', level=3)
        doc.add_heading('Article', level=4)
        doc.add_paragraph('作者：Alice')
        p = doc.add_paragraph('Before ')
        p.add_run('bold').bold = True
        p.add_run(' and ')
        p.add_run('italic').italic = True
        p = doc.add_paragraph()
        first = p.add_run().add_picture(str(self.image))
        first._inline.graphic.graphicData.pic.spPr.get_or_add_xfrm().set('rot', str(90 * 60000))
        p.add_run().add_picture(str(self.image))
        doc.add_paragraph()
        doc.add_paragraph('图1：图片说明', style='Caption')
        doc.add_heading('Subheading', level=5)
        doc.add_heading('Minor heading', level=6)
        doc.add_paragraph('【Bracket heading】')
        doc.add_table(rows=1, cols=1).cell(0, 0).text = 'Table text'
        doc.save(str(self.source))
        self.original_hash = hashlib.sha256(self.source.read_bytes()).digest()

    def test_conversion_output_and_source_untouched(self):
        target = self.root / 'output' / 'converted.md'
        report = []
        output, images = convert(str(self.source), str(target), bracket_subtitle=True, report=report)
        self.assertEqual(images, 2)
        self.assertEqual(Path(output), target)
        text = target.read_text(encoding='utf-8')
        self.assertIn('# 前言：新的开始', text)
        self.assertIn('Before **bold** and *italic*', text)
        self.assertEqual(text.count('![图1：图片说明]'), 2)
        self.assertIn('##### Bracket heading', text)
        self.assertIn('Table text', text)
        self.assertTrue(any('表格' in line for line in report))
        with Image.open(str(target.parent / 'converted_图片' / 'img1.png')) as image:
            self.assertEqual(image.size, (4, 8))
        self.assertFalse((self.root / 'manuscript_图片').exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.original_hash)
        nodes = parse_articles(target)
        self.assertEqual(nodes[0].title, '新的开始')
        self.assertEqual(nodes[-1].author, 'Alice')

    def test_word_direct_to_epub(self):
        style = self.root / 'style.css'
        style.write_text('body { line-height: 1.8; }', encoding='utf-8')
        output = self.root / 'book.epub'
        report = []
        count, _ = build_epub(BookOptions(self.source, self.image, style, output, 'Book', 'Author'), report)
        self.assertEqual(count, 1)
        with zipfile.ZipFile(output) as archive:
            images = [name for name in archive.namelist() if '/body_' in name]
            self.assertEqual(len(images), 2)
            text = archive.read('OEBPS/chapter_04.xhtml').decode('utf-8')
            self.assertIn('<em>italic</em>', text)
            self.assertIn('Table text', text)
        self.assertTrue(report)
        self.assertFalse(self.source.with_suffix('.md').exists())
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).digest(), self.original_hash)

    def test_plain_word_gets_filename_heading(self):
        doc = Document()
        doc.add_paragraph('No styled heading, just body text.')
        doc.save(str(self.source))
        target = self.root / 'plain.md'
        report = []
        convert(str(self.source), str(target), report=report)
        self.assertTrue(target.read_text(encoding='utf-8').startswith('# manuscript\n'))
        self.assertTrue(report)

    def test_leading_unstyled_text_is_preserved(self):
        doc = Document()
        doc.add_paragraph('Leading text')
        doc.add_heading('Article', level=4)
        doc.add_paragraph('Body')
        doc.save(str(self.source))
        target = self.root / 'leading.md'
        convert(str(self.source), str(target), report=[])
        nodes = parse_articles(target)
        self.assertEqual(nodes[0].paragraphs, ['Leading text'])
        self.assertEqual(nodes[1].title, 'Article')


if __name__ == '__main__':
    unittest.main()
