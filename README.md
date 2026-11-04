# epub-maker-cn

一个面向中文 Markdown 文集的 EPUB 制作工具，提供 Windows 图形界面和命令行两种使用方式。

## 功能

- 从 Markdown 原稿自动识别文章标题、作者和正文
- 支持 JPG、PNG 封面
- 在界面中显示并编辑 CSS 样式表
- 生成可见目录页、EPUB3 导航和 EPUB2 NCX 目录
- 兼顾微信读书等阅读软件的目录兼容性
- 自动校验作者、正文、XML、封面声明和目录数量

## 图形界面

Windows 用户双击 `启动电子书制作工具.bat`，选择原稿、封面和样式表，填写书名及作者，然后生成 EPUB。

详细说明见 [电子书制作工具说明.md](./电子书制作工具说明.md)。

## 原稿格式

```markdown
#### 标题：文章标题
**作者：作者名**

正文第一段……
```

## 命令行

```powershell
python .\epub_maker.py `
  --source ".\文集.md" `
  --cover ".\cover.jpg" `
  --style ".\EPUB样式表.md" `
  --output ".\文集.epub" `
  --title "文集名称" `
  --creator "作者或机构"
```

运行环境：Python 3.9 或更高版本，仅使用 Python 标准库。
