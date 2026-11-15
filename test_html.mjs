import fs from 'node:fs/promises';
import path from 'node:path';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import { webcrypto } from 'node:crypto';

const html=await fs.readFile(new URL('./电子书制作工具.html',import.meta.url),'utf8');
const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
const sandbox={TextEncoder,TextDecoder,DecompressionStream,Blob,crypto:webcrypto,console};
vm.createContext(sandbox);
vm.runInContext(script,sandbox,{filename:'电子书制作工具.html'});
const E=sandbox.EpubEngine;
const output=process.argv[2];
if(!output)throw Error('Provide temporary output directory.');
const png=new Uint8Array(Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9xkAAAAASUVORK5CYII=','base64'));
const asset={bytes:png,url:'blob:test'},assets=new Map([['book/图/a (1).png',asset]]);
const text='# 前沿\nPreface body\n## V\n### C\nChapter intro\n#### A & B\n**作者：Alice**\n![A & "B"](<图/a (1).png>)\nText ![inline](图/a%20%281%29.png "Caption") end\n##### Subtitle\nBody\n#### C\n**作者：**\nNo author\n```md\n![](missing.png)\n```';
const nodes=E.parseMarkdown(text);
assert.equal(nodes[0].title,'前言');assert.equal(nodes.length,5);assert.equal(nodes[3].author,'Alice');assert.equal(nodes[4].author,'');
assert.equal(nodes[4].lines.includes('**作者：**'),false);
assert.equal(E.normalizeHeading('前言：我想长出一双新眼睛',1),'我想长出一双新眼睛');
assert.equal(E.normalizeHeading('自序',1),'自序');assert.equal(E.normalizeHeading('前沿科技',1),'前沿科技');
const ctx=E.context(assets,'book/source.md');
const body=E.renderBody(nodes[3],ctx);
assert.equal(ctx.errors.size,0);assert.equal(ctx.used.size,1);assert.equal((body.match(/<img /g)||[]).length,2);
assert.ok(body.includes('alt="A &amp; &quot;B&quot;"'));assert.ok(body.includes('<h2 class="section-title">Subtitle</h2>'));
const previewCtx=E.context(assets,'book/source.md',true);
assert.ok(E.renderBody(nodes[3],previewCtx).includes('src="blob:test"'));
assert.equal(E.context(new Map(),'').errors.size,0);
const missing=E.context(new Map(),'');E.renderBody(nodes[3],missing);assert.equal(missing.errors.size,1);
const base={assets,sourcePath:'book/source.md',title:'Test & Book',creator:'Author',css:'body { color: #333; }',cover:{bytes:png}};
const blob=E.makeEpub({...base,nodes});await fs.writeFile(path.join(output,'html-fixture.epub'),Buffer.from(await blob.arrayBuffer()));
assert.throws(()=>E.makeEpub({...base,nodes,assets:new Map()}),/找不到图片/);
assert.throws(()=>E.makeEpub({...base,nodes:E.parseMarkdown('#### Empty\n')}),/正文为空/);
for(const [source,depth] of [['#### A\nBody',1],['### C\n#### A\nBody',2],['## V\n#### A\nBody',2],['## V\n### C\n#### A\nBody',3]]){
  const tree=E.buildTree(E.parseMarkdown(source));let branch=tree[0],actual=1;while(branch.children.length){actual++;branch=branch.children[0];}assert.equal(actual,depth);
}
// Exercise UI initialization and event handlers with a small in-memory DOM.
// This checks state transitions, not actual browser layout or rendering.
class Element {
  constructor(){this.value='';this.children=[];this.listeners={};this.style={};this.dataset={};this.classList={toggle(){}};}
  append(element){this.children.push(element);}
  replaceChildren(){this.children=[];}
  addEventListener(event,fn){this.listeners[event]=fn;}
  querySelectorAll(){return this.children.filter(e=>e.dataset.index!=null);}
  click(){if(this.onclick)this.onclick();}
}
const elements=new Map();
for(const [,id] of html.matchAll(/\bid="([^"]+)"/g))elements.set(id,new Element());
const document={getElementById:id=>elements.get(id),createElement:()=>new Element()};
let downloadedBlob;
const uiSandbox={TextEncoder,TextDecoder,DecompressionStream,Blob,crypto:webcrypto,console,document,window:{addEventListener(){}},localStorage:{getItem:()=>null,setItem(){}},URL:{createObjectURL:blob=>{downloadedBlob=blob;return 'blob:mock';},revokeObjectURL(){}},setTimeout:fn=>{fn();}};
vm.createContext(uiSandbox);vm.runInContext(script,uiSandbox);
assert.ok(elements.get('preview').srcdoc.includes('把生活写成一本书'));
elements.get('fontSize').value='24';elements.get('fontSize').listeners.input();
assert.ok(elements.get('preview').srcdoc.includes('font-size: 24px'));
elements.get('showToc').click();assert.ok(elements.get('preview').srcdoc.includes('toc-tree'));
elements.get('resetStyle').click();assert.equal(elements.get('fontSize').value,18);
assert.equal(elements.get('subheadingSize').value,1);assert.equal(elements.get('minorHeadingSize').value,0.9);
const sourceFile={name:'sample.md',text:async()=> '# 前言：一个开始\nPreface\n#### Article\nBody\n##### Subheading\nText\n###### Minor heading\nText'};
await elements.get('source').onchange({target:{files:[sourceFile]}});
assert.equal(elements.get('bookTitle').value,'sample');assert.ok(elements.get('preview').srcdoc.includes('一个开始'));
elements.get('outline').children.at(-1).click();assert.ok(elements.get('preview').srcdoc.includes('Article'));
for(const [key,value,selector] of [['titleSize','1.8','.chapter-title'],['subheadingSize','1.3','.content h2.section-title'],['minorHeadingSize','0.9','.content h3.section-title']]){
  elements.get(key).value=value;elements.get(key).listeners.input();
  const css=elements.get('preview').srcdoc;
  assert.ok(css.includes(selector));assert.ok(css.includes('font-size: '+value+'em'));
}
assert.ok(elements.get('preview').srcdoc.includes('<h2 class="section-title">Subheading</h2>'));
assert.ok(elements.get('preview').srcdoc.includes('<h3 class="section-title">Minor heading</h3>'));
elements.get('subheadingSize').value='99';elements.get('subheadingSize').listeners.input();
assert.ok(elements.get('preview').srcdoc.includes('.content h2.section-title { font-size: 3em; }'));
elements.get('subheadingSize').value='1.3';elements.get('subheadingSize').listeners.input();
elements.get('device').value='420';elements.get('device').onchange();assert.equal(elements.get('preview').style.maxWidth,'420px');
await elements.get('cover').onchange({target:{files:[{name:'cover.png',arrayBuffer:async()=>png.buffer}]}});
await elements.get('export').onclick();assert.ok(elements.get('status').textContent.includes('sample.epub'));
await fs.writeFile(path.join(output,'html-headings.epub'),Buffer.from(await downloadedBlob.arrayBuffer()));
if(process.argv[3]){
  const buffer=await fs.readFile(process.argv[3]),word=await E.convertDocx(buffer,{filename:'word-fixture.docx',bracketSubtitle:true});
  assert.ok(word.markdown.includes('Before **bold** and *italic* and ***both***'));
  assert.ok(word.markdown.includes('**inherited** hyperlink'));
  assert.ok(word.markdown.includes('##### Bracket heading'));
  assert.equal(word.assets.size,2);assert.equal((word.markdown.match(/!\[图1：图片说明\]/g)||[]).length,2);
  assert.ok(word.warnings.some(w=>w.includes('表格')));assert.ok(word.markdown.includes('Table text'));
  elements.get('bracketSubtitle').checked=true;
  await elements.get('source').onchange({target:{files:[{name:'word-fixture.docx',arrayBuffer:async()=>buffer}]}});
  assert.equal(elements.get('bookTitle').value,'word-fixture');assert.equal(elements.get('saveMarkdown').disabled,false);
  assert.ok(elements.get('preview').srcdoc.includes('新的开始'));assert.equal(elements.get('conversionNotes').hidden,false);
  await elements.get('saveMarkdown').onclick();await fs.writeFile(path.join(output,'word-markdown.zip'),Buffer.from(await downloadedBlob.arrayBuffer()));
  elements.get('outline').children.find(e=>e.textContent==='Article').click();
  assert.ok(elements.get('preview').srcdoc.includes('<em>italic</em>'));assert.ok(elements.get('preview').srcdoc.includes('<strong><em>both</em></strong>'));assert.ok(elements.get('preview').srcdoc.includes('<img src="blob:mock"'));
  await elements.get('export').onclick();await fs.writeFile(path.join(output,'html-word.epub'),Buffer.from(await downloadedBlob.arrayBuffer()));
  await elements.get('source').onchange({target:{files:[{name:'legacy.doc'}]}});assert.ok(elements.get('status').textContent.includes('另存为 .docx'));
  await assert.rejects(()=>E.convertDocx(new Uint8Array([1,2,3])),/有效的/);
  console.log('PASS: compressed DOCX import, formatting, image extraction, captions, bracket option, table warning, Markdown ZIP and EPUB export');
}
console.log('PASS: UI state tests for initialization, source import, live style changes, chapter selection, mobile width, reset and export');
console.log('PASS: HTML engine, preface, authors, image paths, preview, EPUB export, optional levels, missing-image errors');
