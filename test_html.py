"""Run dependency-free browser engine tests and inspect its actual EPUB output."""
import subprocess
import tempfile
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

from epub_maker import validate_epub


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
        subprocess.run(['node', str(Path(__file__).with_suffix('.mjs')), folder], check=True)
        check(Path(folder) / 'html-fixture.epub', 1)
        check(Path(folder) / 'html-headings.epub', 0)
        with zipfile.ZipFile(Path(folder) / 'html-headings.epub') as archive:
            css = archive.read('OEBPS/style.css').decode('utf-8')
            assert 'font-size: 1.8em; text-align:' in css
            assert '.content h2.section-title { font-size: 1.3em; }' in css
            assert '.content h3.section-title { font-size: 0.9em; }' in css
    print('PASS: ZIP CRC, mimetype, XML, image resources, manifest IDs and all navigation links')
