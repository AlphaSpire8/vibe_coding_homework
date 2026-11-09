# 完整报告

- `report.tex`：中文报告，包含数据整理、四类探索、选题、验证、局限、AI 使用和复现说明。
- `discovery.pdf`、`validation.pdf`：报告使用的两张核心图。

在独立的 TexPage 空白项目中，将 `report.tex` 的全部内容复制到同名文件，上传两个 PDF 图片，三者放在同一目录。设置主文件为 `report.tex`、编译器为 **XeLaTeX**，TeX Live 可选 2025 或更新版本，再编译并导出 PDF。

源码已包含参考文献，使用 Fandol 字体，无需 `.bib`、额外模板或字体文件。姓名、学号、单位集中在导言区。交叉引用初次显示为问号时再编译一次。字体方案见 [TexPage 官方说明](https://www.texpage.com/docs/zh/learning/chapter-5/)。
