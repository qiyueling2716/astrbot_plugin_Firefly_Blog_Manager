"""模块说明见 main.py 顶部文档。"""

from __future__ import annotations




# ============================================================================
# 常量定义
# ============================================================================

DEFAULT_BLOG_ROOT = "/var/www/firefly"
DEFAULT_WEB_ROOT = "/var/www/html"
DEFAULT_SSH_PORT = 22
BUILD_TIMEOUT = 600  # 10分钟
DEPLOY_TIMEOUT = 300  # 5分钟
SSH_KEEPALIVE_INTERVAL = 30
SSH_KEEPALIVE_COUNT_MAX = 3

# 插件名称（与 @register 注册名保持一致，用于 Web API 路由前缀）
PLUGIN_NAME = "astrbot_plugin_Firefly_Blog_Manager"

# 插件版本号（与 metadata.yaml 保持一致，用于 @register 和帮助菜单展示）
PLUGIN_VERSION = "1.8.7"

# ============================================================================
# 对外展示配置文件
# ============================================================================

# 归入"对外展示"管理的配置文件（站点对外展示内容：友链/关于我-社交链接/页脚等），
# 可在插件配置 external_config_files 中追加自定义文件名
EXTERNAL_CONFIG_FILES = [
    "friendsConfig.ts",  # 友链
    "profileConfig.ts",  # 关于我（社交链接等）
    "socialConfig.ts",   # 社交链接（独立文件时）
    "footerConfig.ts",   # 页脚
]

# ============================================================================
# 进阶语法功能元数据定义
# ============================================================================

ADVANCED_SYNTAX_FEATURES = {
    "github_card": {
        "name": "GitHub 仓库卡片",
        "key": "advanced_syntax_github_card",
        "category": "链接增强",
        "pros": [
            "可在文章中嵌入 GitHub 仓库动态信息卡片，展示 Star 数、Fork 数、描述等",
            "自动从 GitHub API 获取最新数据，卡片内容始终为最新",
            "适合技术博客引用开源项目，视觉效果专业",
        ],
        "cons": [
            "依赖 GitHub API 可用性，离线环境无法渲染",
            "每次页面加载都会发起 API 请求，增加加载时间",
            "API 有频率限制，高流量博客可能触发限流",
        ],
        "syntax": '::github{repo="owner/repo"}',
        "example": '::github{repo="CuteLeaf/Firefly"}',
        "guide": "在 Markdown 中直接使用 `::github{repo=\"owner/repo\"}` 语法。"
                "页面加载时，信息会从 GitHub API 获取并渲染为动态卡片。"
                "适合在技术文章中引用开源项目。",
    },
    "admonitions": {
        "name": "提醒框 (Admonitions)",
        "key": "advanced_syntax_admonitions",
        "category": "内容组织",
        "pros": [
            "支持 NOTE/TIP/IMPORTANT/WARNING/CAUTION 等多种类型，语义明确",
            "可选 4 种主题：GitHub、Obsidian、VitePress、Docusaurus",
            "适合突出重要信息、注意事项、警告等，提升文章可读性",
        ],
        "cons": [
            "使用过多会导致文章视觉碎片化，降低阅读流畅性",
            "不同主题语法略有差异，切换主题后需检查兼容性",
            "Docusaurus 风格使用 `:::` 语法，与 GitHub 风格不兼容",
        ],
        "syntax": "> [!TYPE] 标题\n> 内容",
        "example": "> [!NOTE] 注意\n> 这是一个重要提示。\n\n> [!WARNING] 警告\n> 此操作不可逆！",
        "guide": "Firefly 支持 4 种提醒框主题（GitHub/Obsidian/VitePress/Docusaurus），"
                "默认使用 GitHub 风格。基本语法：`> [!TYPE] 标题` + `> 内容`。"
                "可用类型：NOTE、TIP、IMPORTANT、WARNING、CAUTION。"
                "Obsidian 风格额外支持 abstract、info、todo、success、question、failure、danger、bug、example、quote。",
    },
    "spoiler": {
        "name": "剧透文本",
        "key": "advanced_syntax_spoiler",
        "category": "内容组织",
        "pros": [
            "可隐藏敏感或剧透内容，用户点击才显示，增强互动性",
            "支持内嵌 Markdown 语法（加粗、斜体等），灵活性高",
            "适合隐藏答案、剧透、额外信息等",
        ],
        "cons": [
            "移动端触控体验不佳（点击区域小），需要较大的点击目标",
            "被隐藏内容可能被搜索引擎忽略，不利于 SEO",
            "滥用会导致用户阅读体验下降",
        ],
        "syntax": ":spoiler[被隐藏的内容]",
        "example": "答案：:spoiler[42]",
        "guide": "使用 `:spoiler[内容]` 语法隐藏文本。"
                "隐藏内容中支持 Markdown 格式，如 `:spoiler[**加粗**的秘密]`。"
                "用户点击/触摸隐藏区域即可显示内容。",
    },
    "image_grid": {
        "name": "图片画廊网格",
        "key": "advanced_syntax_image_grid",
        "category": "多媒体",
        "pros": [
            "可将 2-4 张图片并排展示，自动裁剪对齐，视觉整齐",
            "响应式布局，自动适配不同屏幕尺寸",
            "图注恒定底端对齐，适合照片对比、画廊展示",
        ],
        "cons": [
            "比例不一致的图片会被裁剪（object-cover），完整内容需点击灯箱查看",
            "仅支持 2-4 张图片，无法展示更多",
            "被裁剪后部分图片内容不可见，建议使用相同比例的图片",
        ],
        "syntax": "[grid]\n![图片1](./img1.jpg)\n![图片2](./img2.jpg)\n[/grid]",
        "example": "[grid]\n![示例一](./firefly1.avif)\n![示例二](./firefly2.avif)\n[/grid]",
        "guide": "使用 `[grid]` 和 `[/grid]` 标签包裹图片。"
                "支持 2-4 张图片并排，系统自动响应式布局。"
                "同一行图片若比例不一致，会自动裁剪居中。"
                "建议使用相同长宽比的图片。",
    },
    "code_blocks": {
        "name": "代码块进阶 (Expressive Code)",
        "key": "advanced_syntax_code_blocks",
        "category": "代码展示",
        "pros": [
            "支持编辑器/终端框架，可设置文件名或终端标题",
            "支持行号、行高亮标记(diff/ins/del)、行标签",
            "支持可折叠区域、自动换行、ANSI 转义序列渲染",
            "适合技术教程和代码演示",
        ],
        "cons": [
            "语法复杂，配置项多，新手学习成本高",
            "部分功能依赖代码块元数据（如 title=\"xxx\"、showLineNumbers 等），需额外记忆",
            "Diff 语法与特定语言语法高亮混用时需注意兼容性",
        ],
        "syntax": "```lang title=\"文件名\" showLineNumbers\n// 代码\n```",
        "example": '```js title="app.js" showLineNumbers\n// 第1行\nconsole.log("Hello")\n// 第3行 - 标记\n```',
        "guide": "Firefly 使用 Expressive Code 渲染代码块。主要配置：\n"
                "- `title=\"文件名\"` — 显示编辑器框架和文件名\n"
                "- `title=\"Terminal window\"` — 终端框架样式\n"
                "- `frame=\"none\"` — 无框架\n"
                "- `showLineNumbers` — 显示行号\n"
                "- 行标记：在代码块元数据中标注行号，如 `\"第3行\"` 或 `\"7-8\"`\n"
                "- 标记类型：`mark`（默认蓝）、`ins`（绿色插入）、`del`（红色删除）\n"
                "- diff 语法：以 `+`/`-` 开头自动识别为 diff 标记\n"
                "- 折叠：在代码块中自动折叠样板代码",
    },
    "mermaid": {
        "name": "Mermaid 图表",
        "key": "advanced_syntax_mermaid",
        "category": "图表绘制",
        "pros": [
            "用纯文本描述即可生成多种图表，无需外部工具",
            "支持流程图、时序图、甘特图、类图、状态图、饼图",
            "与 Markdown 原生集成，版本管理友好",
        ],
        "cons": [
            "复杂图表语法冗长，维护成本高",
            "不支持所有图表类型（如 ER 图、部署图需用 PlantUML）",
            "渲染依赖客户端 JavaScript，某些阅读器不支持",
        ],
        "syntax": "```mermaid\n图表类型\n  语法描述\n```",
        "example": "```mermaid\nflowchart TD\n  A[开始] --> B{条件}\n  B -->|是| C[结果1]\n  B -->|否| D[结果2]\n```",
        "guide": "在代码块中指定 `mermaid` 语言即可。支持的图表类型：\n"
                "- `flowchart TD/LR` — 流程图\n"
                "- `sequenceDiagram` — 时序图\n"
                "- `gantt` — 甘特图\n"
                "- `classDiagram` — 类图\n"
                "- `stateDiagram` — 状态图\n"
                "- `pie` — 饼图\n\n"
                "Mermaid 适合轻量级图表，如需更丰富的图表类型（ER图、C4图等），"
                "请使用 PlantUML。",
    },
    "plantuml": {
        "name": "PlantUML 图表",
        "key": "advanced_syntax_plantuml",
        "category": "图表绘制",
        "pros": [
            "图表类型比 Mermaid 更丰富：活动图、用例图、组件图、部署图、ER图、C4架构图",
            "支持亮暗主题自动切换，缩放、拖拽和全屏交互",
            "语法高度结构化，适合软件工程文档",
        ],
        "cons": [
            "依赖外部 PlantUML 服务器渲染 SVG，内网环境可能需要自建服务",
            "渲染速度比 Mermaid 慢（需要网络请求）",
            "语法比 Mermaid 更复杂，学习曲线更陡",
        ],
        "syntax": "```plantuml\n@startuml\n  图表定义\n@enduml\n```",
        "example": "```plantuml\n@startuml\nactor User\nUser -> (登录)\nUser -> (查看文章)\n@enduml\n```",
        "guide": "在代码块中指定 `plantuml` 语言。Firefly 会在构建时编码并生成 SVG。\n"
                "支持的图表类型：\n"
                "- 活动图 (activity)\n"
                "- 时序图 (sequence)\n"
                "- 用例图 (usecase)\n"
                "- 类图 (class)\n"
                "- 组件图 (component)\n"
                "- 部署图 (deployment)\n"
                "- ER 图 (entity)\n"
                "- 状态图 (state)\n"
                "- C4 架构图\n\n"
                "PlantUML 适合需要丰富图表类型的软件工程文档。",
    },
    "katex": {
        "name": "KaTeX 数学公式",
        "key": "advanced_syntax_katex",
        "category": "学术/数学",
        "pros": [
            "渲染速度极快，不依赖外部服务",
            "支持行内公式、块级公式、矩阵、极限、求和、化学方程式",
            "适合数学、物理、计算机科学等技术博客",
        ],
        "cons": [
            "仅支持 LaTeX 数学模式子集，不支持 amsmath 的某些高级宏",
            "复杂公式占用较多垂直空间，可能影响阅读节奏",
            "不支持 \\newcommand 等自定义命令",
        ],
        "syntax": "$行内公式$ 或 $$块级公式$$",
        "example": "欧拉公式：$e^{i\\pi} + 1 = 0$\n\n"
            "质能方程：\n\n$$\nE = mc^2\n$$\n\n"
            "矩阵：\n\n$$\n\\begin{pmatrix}\na & b \\\\\nc & d\n\\end{pmatrix}\n$$\n\n"
            "化学方程式：\n\n$$\n\\ce{CH4 + 2O2 -> CO2 + 2H2O}\n$$",
        "guide": "行内公式使用单 `$` 包裹，块级公式使用双 `$$` 包裹。\n"
                "支持常见 LaTeX 数学语法：\n"
                "- 分数：`\\frac{分子}{分母}`\n"
                "- 矩阵：`\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}`\n"
                "- 求和：`\\sum_{n=1}^{\\infty}`\n"
                "- 极限：`\\lim_{x \\to 0}`\n"
                "- 化学方程式：`\\ce{CH4 + 2O2 -> CO2 + 2H2O}`\n\n"
                "⚠️ 反斜杠转义规则（矩阵和方程式最常见的出错点，务必遵守）：\n"
                "- 最终写入 .md 文件时，LaTeX 命令只用单个反斜杠：`\\frac{1}{2}`、`\\begin{pmatrix}`、`\\ce{CH4 + 2O2 -> CO2 + 2H2O}`\n"
                "- 矩阵换行、公式内换行使用双反斜杠 `\\\\`；多行矩阵建议写成示例中的多行形式\n"
                "- 内容作为工具参数传入时是 JSON 字符串，每个反斜杠要翻倍转义：文件中的 `\\begin{pmatrix}` 在 JSON 中写成 `\\\\begin{pmatrix}`，行分隔符 `\\\\` 在 JSON 中写成 `\\\\\\\\`。插件会把收到的参数原样写入文件，不会二次转义\n\n"
                "更多语法参考 KaTeX 官方文档。",
    },
}

