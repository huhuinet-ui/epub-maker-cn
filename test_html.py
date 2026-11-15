"""Run dependency-free browser engine tests and inspect its actual EPUB output."""
import subprocess
import tempfile
import zipfile
import base64
from pathlib import Path
from xml.etree import ElementTree as ET

from epub_maker import validate_epub


def word_fixture(folder):
    """A small deflated DOCX package, no document-authoring dependency needed."""
    path = Path(folder) / 'word-fixture.docx'
    document = '''<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"><w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading4"/></w:pPr><w:r><w:t>前沿：新的开始</w:t></w:r></w:p><w:p><w:r><w:t>Preface body</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading2"/></w:pPr><w:r><w:t>Volume</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading3"/></w:pPr><w:r><w:t>Chapter</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading4"/></w:pPr><w:r><w:t>Article</w:t></w:r></w:p>
    <w:p><w:r><w:t>作者：Alice</w:t></w:r></w:p>
    <w:p><w:r><w:t xml:space="preserve">Before </w:t></w:r><w:r><w:rPr><w:b/></w:rPr><w:t>bold</w:t></w:r><w:r><w:t xml:space="preserve"> and </w:t></w:r><w:r><w:rPr><w:i/></w:rPr><w:t>italic</w:t></w:r><w:r><w:t xml:space="preserve"> and </w:t></w:r><w:r><w:rPr><w:b/><w:i/></w:rPr><w:t>both</w:t></w:r></w:p>
    <w:p><w:r><w:rPr><w:rStyle w:val="Strong"/></w:rPr><w:t>inherited</w:t></w:r><w:hyperlink r:id="link"><w:r><w:t xml:space="preserve"> hyperlink</w:t></w:r></w:hyperlink></w:p>
    <w:p><w:r><w:drawing><a:blip r:embed="img"/></w:drawing></w:r><w:r><w:drawing><a:blip r:embed="img"/></w:drawing></w:r></w:p>
    <w:p/><w:p><w:pPr><w:pStyle w:val="Caption"/></w:pPr><w:r><w:t>图1：图片说明</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading5"/></w:pPr><w:r><w:t>Subheading</w:t></w:r></w:p>
    <w:p><w:pPr><w:pStyle w:val="Heading6"/></w:pPr><w:r><w:t>Minor heading</w:t></w:r></w:p>
    <w:p><w:r><w:t>【Bracket heading】</w:t></w:r></w:p>
    <w:tbl><w:tr><w:tc><w:p><w:r><w:t>Table text</w:t></w:r></w:p></w:tc></w:tr></w:tbl>
    </w:body></w:document>'''
    styles = '<w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">' + ''.join(
        '<w:style w:type="paragraph" w:styleId="Heading{0}"><w:name w:val="heading {0}"/></w:style>'.format(i) for i in range(1, 7)) + '<w:style w:styleId="Caption"><w:name w:val="Caption"/></w:style><w:style w:styleId="Strong"><w:rPr><w:b/></w:rPr></w:style></w:styles>'
    with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr('word/document.xml', document)
        archive.writestr('word/styles.xml', styles)
        archive.writestr('word/_rels/document.xml.rels', '<Relationships><Relationship Id="img" Target="media/img.png" Type="image"/><Relationship Id="link" Target="https://example.com" TargetMode="External" Type="hyperlink"/></Relationships>')
        archive.writestr('word/media/img.png', base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII='))
    return path


def check(path, expected_images):
    validate_epub(path)
    with zipfile.ZipFile(path) as archive:
        assert archive.testzip() is None
        assets = [name for name in archive.namelist() if name.startswith('OEBPS/images/body_')]
        assert len(assets) == expected_images
        opf = ET.fromstring(archive.read('OEBPS/content.opf'))
        ns = '{http://www.idpf.org/2007/opf}'
        items = opf.findall(ns + 'manifest/' + ns + 'item')
        assert len({item.get('id') for item in items}) == len(items)
        for item in items:
            assert 'OEBPS/' + item.get('href') in archive.namelist()
        links = 0
        for name in archive.namelist():
            if name.endswith('.xhtml'):
                tree = ET.fromstring(archive.read(name))
                for a in tree.findall('.//{http://www.w3.org/1999/xhtml}a'):
                    assert 'OEBPS/' + a.get('href') in archive.namelist()
                links += len(tree.findall('.//{http://www.w3.org/1999/xhtml}img'))
        assert links >= expected_images


if __name__ == '__main__':
    with tempfile.TemporaryDirectory() as folder:
        fixture = word_fixture(folder)
        subprocess.run(['node', str(Path(__file__).with_suffix('.mjs')), folder, str(fixture)], check=True)
        check(Path(folder) / 'html-fixture.epub', 1)
        check(Path(folder) / 'html-headings.epub', 0)
        with zipfile.ZipFile(Path(folder) / 'html-headings.epub') as archive:
            css = archive.read('OEBPS/style.css').decode('utf-8')
            assert 'font-size: 1.8em; text-align:' in css
            assert '.content h2.section-title { font-size: 1.3em; }' in css
            assert '.content h3.section-title { font-size: 0.9em; }' in css
        check(Path(folder) / 'html-word.epub', 2)
        with zipfile.ZipFile(Path(folder) / 'word-markdown.zip') as archive:
            text = archive.read('word-fixture.md').decode('utf-8')
            assert '# 前言：新的开始' in text
            assert 'Before **bold** and *italic* and ***both***' in text
            assert '图/img1.png' in archive.namelist()
    print('PASS: ZIP CRC, mimetype, XML, image resources, manifest IDs and all navigation links')
