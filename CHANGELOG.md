# Changelog

所有显著变更都会记录在此文件。

## [1.8.7] - 2026-08-08

### 修复（破坏性）

- **deploy.sh 同目录部署会删除博客源码**：默认配置 `LOCAL_BLOG_ROOT` 与 `WEB_ROOT` 均为 `/var/www/firefly`，原 `deploy_files` 先 `rm -rf "$web_root"/*` 再复制，会把刚构建的 `dist/` 及整个博客源码（src/、node_modules、package.json 等）一并删除，随后 `cp` 报错。现在检测到部署目录与博客目录相同时，先把构建产物与源码关键项（src/public/node_modules/package.json 等）暂存移出，清空后再恢复，源码与 dist 均保留；`deploy_remote` 的同目录场景改为仅覆盖部署并输出警告（判断在本地完成，避免远端变量为空导致分支失效）

### 优化

- **部署脚本（deploy.sh / deploy.ps1）网络与稳定性大修**：
  - 克隆改为浅克隆（`--depth 1`），自动依次尝试内置 GitHub 镜像（ghfast.top / gh-proxy.com / ghproxy.net）再回退官方源，每个源有 `GIT_CLONE_TIMEOUT` 超时，不再静默无限等待；失败自动清理残留目录
  - 所有 curl / git / ssh / rsync / scp 操作统一带超时与保活（ServerAlive），不会"莫名其妙卡住"
  - `read` 交互确认在非交互终端（CI / 后台）自动跳过，不再永久挂起
  - 新增 `USE_CN_MIRROR=1` 一键启用国内加速：npm/pnpm 自动切换 npmmirror 源（可被 `NPM_REGISTRY` 覆盖），官方源失败自动重试镜像
  - 博客目录写入 `.npmrc`（fetch 重试 + 120s 超时策略），pnpm 不再静默挂起
  - 远程构建命令受 `REMOTE_CMD_TIMEOUT`（默认 3600s）保护
  - 修复 deploy.sh 为 LF 行尾（原 CRLF 无法直接 bash 运行）；deploy.ps1 克隆不再吞掉输出（可看到进度）
  - 修复 `get_cpu_usage` 在 mpstat/top 路径输出浮点数（如 20.37）导致 `[[ -ge ]]` 整数比较报错，统一取整输出
  - 新增配置项：`CLONE_DEPTH` / `GIT_CLONE_TIMEOUT` / `NET_CONNECT_TIMEOUT` / `REMOTE_CMD_TIMEOUT` / `AUTO_YES` / `RSYNC_TIMEOUT`，示例见 `deploy.conf.example`

---

## [1.8.6] - 2026-08-08

### 新增

- **随机壁纸背景**：页面启动时自动从已部署博客的 `src/assets/images/DesktopWallpaper`（桌面端，宽度 > 768px）或 `MobileWallpaper`（移动端）随机选取一张作为背景，配暗色渐变遮罩与毛玻璃卡片，保证内容可读性
- **未部署提示**：未检测到已部署的博客时，页面顶部显示提示条，指引使用插件目录下的 `deploy.sh` / `deploy.ps1` 脚本部署

---

## [1.8.5] - 2026-08-08

### 修复

- **移除选中时的蓝色焦点框**：文件树目录/文件行与按钮点击后不再显示浏览器默认 outline，键盘 Tab 导航仍保留荧光绿焦点指示（`:focus-visible`）

---

## [1.8.4] - 2026-08-08

### 改进（界面动效与质感）

- **按钮按压动画**：所有按钮点击时轻微缩放（0.94），悬停背景/边框平滑过渡，移动端触控有反馈
- **文件夹展开丝滑**：文件树目录展开/折叠改为 max-height + 透明度缓动动画（0.32s 贝塞尔曲线），竖线导轨淡入，子项依次淡入，箭头平滑旋转
- **标签页切换淡入**：切换标签时面板淡入上移
- **状态卡片微浮起**：桌面端悬停卡片轻微上浮并带柔和阴影

---

## [1.8.3] - 2026-08-08

### 变更

- **移除「站点配置」标签页**：页面只保留 状态 / 文件管理 / 对外展示 三个标签；配置文件均可通过文件管理直接打开编辑（含 README 文档）

### 修复

- **图片（png/jpg/gif/webp/avif 等）打不开**：此前按 UTF-8 文本读取二进制图片导致解码失败；现改为二进制读取并以内嵌图片预览显示（8MB 上限），文件类型徽标补充 avif/bmp/ico
- **左上角 logo 显示绿色背景**：图标容器 CSS 残留绿色渐变背景，已移除，恢复透明底

---

## [1.8.2] - 2026-08-08

### 改进（文件管理视觉引导）

- **文件类型徽标**：文件树中按扩展名显示彩色徽标（TS / 文档 / JSON / 图片 / CSS / JS / VUE），一眼区分文件类型
- **目录层级引导**：目录行加粗高亮、文件行缩进对齐，子目录左侧显示竖线导轨，展开的目录箭头旋转 + 整行保持强调色
- **README 高亮**：文件管理树中 README.md / .md / .txt 显示蓝色「文档」徽标，文件名蓝色加粗（配置页原有高亮保持一致）

---

## [1.8.1] - 2026-08-08

### 修复

- **「对外展示」内容加载不出来（白屏）**：`renderExtTable` 调用未定义的 `externalTableBody` 导致 `ReferenceError`，初始化中断后友链等列表永远停留在"正在加载..."；已补上函数定义
- **关于我（profileConfig.ts）无法在「对外展示」中编辑**：将其加入默认 `external_config_files`（其 `links` 数组字段为受支持的列表目标）

---

## [1.8.0] - 2026-08-08

### 新增

- **「对外展示」页**（原友链管理页扩展）：集中管理对外展示内容——友链（friendsConfig.ts）、社交链接、页脚等，支持**数组导出**（如 `export const friendsConfig: FriendLink[] = [...]`）与**对象中的列表字段**两类目标，通用表格编辑（布尔/数字/数组/对象值自动识别，对象值以 JSON 编辑）
- **配置分类**：对外展示类配置文件（默认 friendsConfig.ts / socialConfig.ts / footerConfig.ts，可在插件配置 `external_config_files` 中追加）从站点配置页归入「对外展示」页，配置页只保留核心配置对象
- 左上角图标更换为插件 logo.png，整体配色（浅色/深色主题）改为 logo 的荧光绿-青绿系

### 修复

- **带 TS 类型标注的真实配置（如 `export const siteConfig: SiteConfig = {...}`）无法结构化保存**：`rebuild_ts_config` 误将 `=` 与 `{` 之间的类型标注判为"非纯对象配置"
- **配置页点击对象完全无反应**：配置列表改为整行可点击（此前只有文件名文本可点，点击行其他区域无响应），并增加加载反馈 toast
- 解析器增强：支持单引号字符串、嵌套数组正确截取，遍历全部导出候选（取第一个可解析的对象），friendsConfig.ts 这类"对象+数组"混合导出的文件可正确归类

---

## [1.7.0] - 2026-08-08

### 改进（触控与新手易用性）

- **文件树整行可点击**：文件夹点击整行直接向下展开/折叠（不再需要点小箭头），文件点击整行打开；行高与点击区加大（手机端 44px），带按下反馈
- **配置文件列表高亮文档**：`src/config/` 下的 README.md / .md / .txt 现在会显示在配置列表中，以蓝色「文档」徽标高亮，点击跳转文件管理查看；动态文件（非标准对象）行为保持不变
- **未保存修改保护**：文件/配置有未保存修改时，切换文件或关闭页面会弹确认，防止误触丢失编辑内容
- **手机端编辑器优化**：编辑区字号加大至 14px、工具栏按钮触控区加大

---

## [1.6.1] - 2026-08-08

### 修复

- **管理页面全部请求失败的严重问题**：页面代码按 `{status, message, data}` 外层结构判断 bridge 返回值，但 AstrBot Plugin Page bridge 成功时返回的是**内层 `data` 字段**（无 `status` 包装）、失败时 `reject(Error(message))`，导致页面所有标签页（状态、文件、友链、站点配置）始终显示"未知错误"（HTTP 实际为 200）。现已统一包装 bridge 调用：成功补 `{status:"ok", data}`，失败提取 `e.message` 展示真实错误
- 文件上传同理适配（`bridge.upload` 直接返回 `{path}`）

### 新增

- Web API 后端异常兜底：handler 异常时记录完整堆栈，并将真实错误返回给页面显示

---

## [1.6.0] - 2026-08-08

### 新增

- **站点配置管理页签**：Dashboard 页面新增「站点配置」，通用结构化编辑 `src/config/` 下的纯对象配置文件（siteConfig、footerConfig、sponsorConfig、commentConfig、announcementConfig 等）：
  - 递归表单编辑嵌套对象、数组（对象数组支持增删元素）、字符串/数字/布尔值，长文本自动使用多行输入
  - 枚举引用、函数调用、模板字符串等动态值（如 `lang: SITE_LANG`、`NavBarSearchMethod.GlobalSearch`）标记为只读并在保存时逐字保留
  - 含动态逻辑的文件（如 navBarConfig 中函数生成的导航栏配置）不会结构化导出，点击后自动跳转「文件管理」直接编辑；`friendsConfig` 数组仍由「友链管理」专属编辑，`friendsPageConfig` 可在此结构化编辑
  - 保存仅重建对应 `export const` 块，文件其余部分（import、注释、其他导出）原样保留；对象内部的注释会被规范化移除并在页面明确提示
- **Web API 新增 3 个接口**：`config-files`（GET，列出 src/config 下各配置文件及可解析状态）、`config-file`（GET/POST，结构化读取/保存），与既有文件管理共用路径安全校验，写入同样受 src 根边界保护

### 优化

- **通用 TS 对象解析器**：新增递归解析/重建（支持双/单引号字符串转义、嵌套对象与数组、数字/布尔/null/undefined），复用注释剥离与括号匹配工具，与友链解析保持一致的健壮性

---

## [1.5.0] - 2026-08-08

### 新增

- **Dashboard 管理页面（WebUI Pages）**：新增 `pages/manager/` 页面，在 AstrBot 面板「插件详情 → 页面」中打开，提供三大功能：
  - **博客状态**：展示部署模式、博客目录（已配置/自动检测/未找到）、Firefly 检测结果、构建状态、文章数量、内存状态与关键配置
  - **文件管理**：以树形结构浏览并编辑博客 `src/` 目录下的全部资源（读取、编辑保存、新建、删除、上传，二进制资源限制预览与文本覆盖），支持本地与 remote_build 远端 SFTP 两种模式，路径穿越等越界操作被严格拦截
  - **友链管理**：图形化编辑 `src/config/friendsConfig.ts` 中的友链列表（标题、网址、描述、头像、标签、权重、启用状态），保存时仅重建 `friendsConfig` 数组，页面配置与注释等其他内容原样保留
- **博客目录自定义**：未自动检测到博客时，可在页面中手动填写博客根目录并保存（写入 `local_blog_root` 配置并重载组件），也可一键重新自动检测
- **Web API 后端**：基于 AstrBot 框架 `context.register_web_api()` 注册 10 个接口（`/{PLUGIN_NAME}/status、config、files、file、file/delete、file/upload/<path:dir>、links`），复用 `astrbot.api.web` 请求封装与响应工具，新增 `webui.py` 模块与 `PLUGIN_NAME` 常量

### 优化

- **文件管理安全**：所有网页文件操作均以博客 `src/` 根目录为边界做规范化校验（拒绝 `..`、绝对路径、Windows 路径穿越），上传文件名经 basename 消毒，二进制扩展名禁止文本覆盖
- **友链解析健壮性**：friendsConfig.ts 解析/重建支持字符串与注释感知（`//`、`/* */`、引号内特殊字符均正确处理），未知字段自动保留，解析失败时给出明确错误而非破坏文件

---

## [1.4.1] - 2026-08-08

### 架构重构

- **模块化拆分**：将 3651 行的单一 `main.py` 按职责拆分为 `constants.py`、`models.py`、`common.py`、`executors.py`、`filesystem.py`、`blog_manager.py`、`build_deploy.py`，`main.py` 仅保留插件主类（Star）、命令与 LLM 工具；各模块通过相对导入协作，功能与行为完全兼容
- **版本统一**：新增 `PLUGIN_VERSION` 常量作为唯一版本来源，`@register` 装饰器与 `/博客帮助` 菜单统一展示 `1.4.1`（此前注册版本停留在 1.3.5）

### 修复

- **remote_build 部署自毁缺陷**：修复 `_deploy_remote()` 中 `rm -rf {remote_blog_root}/*` 会先删除包含 `dist/` 的博客仓库再复制的致命问题；现在构建产物从 `remote_blog_root/dist/` 复制到独立的 `remote_web_root`，并增加「两者配置相同」的安全校验
- **deploy_blog 远端检查路径错误**：remote_build 模式下检查构建产物时使用远端仓库路径 `remote_blog_root`（此前误用本地 `local_blog_root`）
- **投稿缓存迁移**：`_submissions_cache.json` 从插件目录迁移至 AstrBot 框架标准数据目录（`StarTools.get_data_dir()`，即 `data/plugin_data/`），首次启动自动迁移旧文件，避免插件更新丢失数据
- **KaTeX 进阶语法文档转义错误**：修复 `get_syntax_guide` 工具返回的 KaTeX 缺点说明中 `\newcommand` 被 Python 误解析为换行的问题；示例补充了多行矩阵（`\begin{pmatrix}` + `\\` 行分隔符）和化学方程式（`\ce{...}`）写法（与 Firefly 官方示例文章一致）；详细指南新增反斜杠转义规则说明（文件内单反斜杠命令、双反斜杠换行、工具参数 JSON 中反斜杠翻倍），避免 LLM 生成矩阵/方程式时转义错误

### 新增

- **构建并发锁**：实现 `allow_build_concurrent` 配置（此前仅展示未生效）。为 `false` 时同一时间只允许一个构建任务，并构建期间返回明确提示，避免多进程竞争内存
- **构建内存限制**：实现 `build_memory_limit` 配置（此前仅展示未生效）。设置为非零值时通过 `NODE_OPTIONS=--max-old-space-size` 限制 Node.js 堆内存上限

### 优化

- **SSH 探测开销**：`RemoteExecutor` 不再在每条命令前发送 keep-alive 探测，改为距上次成功交互超过 30 秒才探测（asyncssh 自身 keepalive 仍生效），多步远程操作延迟显著降低
- **权限判定框架优先**：`_check_admin_permission` 优先使用 AstrBot 框架标准接口 `event.is_admin()`（平台管理员角色），回退到 `admin_umo` 配置匹配，与权限设计文档保持一致
- **代码去重**：移除 `FireflyBlogManager` 中与 `BuildDeployManager` 重复的 `_is_firefly_blog`；抽取 `get_user_id()` 统一 4 处用户 ID 提取；抽取 `_search_posts()` 供 LLM 工具与显式命令共用；投稿状态文案/图标映射统一收敛到 `models.py`
- **依赖导入整理**：函数内 `import uuid/time/tempfile/json/psutil` 移至模块顶部（psutil 保持可选，缺失时优雅降级）；`_check_memory_status()` 返回值简化
- **超时常量**：构建/部署超时使用统一的 `BUILD_TIMEOUT`/`DEPLOY_TIMEOUT` 常量

### 文档

- **README 权限章节重写**：删除已不存在的 `allow_only_owner`/`admin_users`/`owner_user_id` 配置描述，改为实际生效的 `admin_umo` 配置与「框架 `event.is_admin()` 优先」的验证顺序
- **README 部署模式说明**：明确 `remote_build` 的部署目标为 `remote_web_root`；项目结构章节更新为模块化后的目录树

---

## [1.4.0] - 2026-06-27

### 新增

- **投稿系统完善**：新增 `Submission` 数据模型，支持投稿持久化缓存；新增 `delete_submission`、`retract_submission`、`submission_stats` LLM 工具；新增 `/博客投稿` 和 `/我的投稿` 显式指令；投稿列表支持按状态筛选
- **AI 初审功能**：新增 `enable_ai_review` 配置项，开启后投稿提交时自动调用 LLM 进行内容质量评估（评分、优点、问题、改进建议），管理员可直接查看初审结果决定过审或复审；新增 `ai_review_submission` 手动触发初审工具
- **进阶语法支持**：新增 `enable_advanced_syntax` 总开关及 8 个子开关（GitHub 卡片、Admonitions、Spoiler、图片画廊、Expressive Code、Mermaid、PlantUML、KaTeX），每个子开关标注优劣；新增 `list_advanced_syntax` 和 `get_syntax_guide` LLM 工具
- **帮助菜单**：新增 `/博客帮助` 显式指令，展示所有 LLM 工具、命令、权限级别、部署模式和快速上手示例

### 安全修复

- **SSH 主机密钥验证**：移除 `known_hosts=None`，恢复 asyncssh 默认主机密钥验证；新增 `ssh_known_hosts_path` 配置项；`StrictHostKeyChecking` 从 `no` 改为 `yes`/`accept-new` 可配置，默认启用严格验证
- **密码安全**：密码不再通过 `export SSHPASS` 环境变量传递，改为写入临时文件（权限 `0o600`）通过 `sshpass -f` 读取，执行后自动删除，避免密码出现在进程列表和 shell 命令中
- **命令注入防护**：新增 `_validate_shell_command()` 检测 `$(...)` 和反引号命令替换；所有部署命令中的用户可控参数使用 `shlex.quote()` 标准转义
- **日志脱敏**：新增 `_sanitize_command()` 函数，所有包含命令的日志输出均过滤密码等敏感信息；SSH 连接日志不再记录主机名和端口
- **路径遍历防护**：`_validate_path()` 使用 `os.path.realpath()` 规范化路径，防止 `../` 路径遍历攻击
- **文件权限显式设置**：`write_file` 和 `_save_submissions` 写入后调用 `os.chmod` 设置 `0o644`，投稿缓存文件同理
- **错误信息脱敏**：命令执行异常和 AI 初审异常不再返回 `str(e)` 详细信息给用户，改为通用提示消息，详细错误仅记录在日志中
- **权限信息保护**：权限拒绝时不再返回用户 UMO 标识；未配置 `admin_umo` 时启动输出警告日志
- **UUID 增强**：投稿 ID 从 8 位 hex 增加到 12 位 hex，碰撞概率降低约 1600 万倍


---

## [1.3.4] - 2026-06-23

### 修复

- **配置文件清理**：更新 `_conf_schema.json` 和 `README.md`，移除过时的管理员配置描述，明确说明 `owner_user_id` 和 `admin_users` 仅作为回退配置使用
- **文档更新**：更新 README.md 中的权限控制说明，强调插件优先复用 AstrBot 框架的管理员系统

### 改进

- **权限验证逻辑优化**：完善 `_is_admin()` 方法，优先使用框架提供的权限检查方法，只有当框架方法不可用时才回退到配置文件

---

## [1.3.3] - 2026-06-23

### 修复

- **Context.config 属性兼容**：修复新版 AstrBot 框架中 `Context` 对象不再提供 `config` 属性的问题，添加 `hasattr` 检查防止 `AttributeError`
- **装饰器异步兼容性**：修复装饰器不兼容同步返回值的问题，支持异步生成器、同步生成器和单个返回值三种形式
- **SSH 连接状态管理**：增强 `RemoteExecutor` 类，添加 `ConnectionStatus` 枚举、连接状态维护、并发连接保护和 `reset_connection()` 方法
- **权限检查冗余移除**：移除重复的 `@filter.permission_type(filter.PermissionType.ADMIN)` 装饰器，统一使用自定义的 `@require_permission()` 装饰器
- **路径处理一致性**：将 `BlogManager` 中的 `posixpath` 改为 `os.path`，提高跨平台兼容性
- **配置参数校验**：在 `BuildDeployManager.__init__` 中添加部署模式和路径配置的校验逻辑
- **资源检查可移植性**：改进 `_check_system_resources` 方法，添加跨平台磁盘路径选择和更完善的异常处理
- **管理员权限检查**：修复未正确复用 AstrBot 框架管理员列表的问题，优先使用框架提供的 `event.is_admin()`、`event.is_owner()`、`context.is_admin()`、`context.is_owner()` 方法

### 改进

- **日志记录增强**：在文章创建等关键操作中添加详细日志记录，便于问题排查
- **SSH 连接错误处理**：增强连接失败时的状态管理和清理逻辑
- **配置校验增强**：添加 `_validate_path()` 方法校验路径配置的有效性
- **权限系统重构**：新增 `_is_admin()` 方法复用框架权限系统，`_is_admin_from_config()` 作为回退方案

---

## [1.3.2] - 2026-06-23

### 新增

- **管理员列表支持**：新增 `admin_users` 配置项，支持配置多个管理员用户 ID，所有管理员都拥有主人级别的权限
- **多来源管理员提取**：`_get_admin_user_ids()` 方法支持从多个来源提取管理员列表（插件配置 admin_users、插件配置 owner_user_id、AstrBot 全局配置 owner_id、AstrBot 全局配置管理员列表）

### 改进

- `_check_permission` 方法改为支持管理员列表，权限检查逻辑从单个用户 ID 改为用户 ID 集合
- 更新配置文档，添加 `admin_users` 配置说明和管理员来源优先级说明

---

## [1.3.1] - 2026-06-23

### 修复

- **装饰器元数据**：为所有装饰器添加 `functools.wraps`，保留原函数的 `__name__`、`__doc__` 等属性，便于 AstrBot 反射
- **权限检查边界情况**：支持多种用户 ID 字段名（user_id、sender_id、from_id），并统一转为字符串比较避免类型不一致问题
- **文件路径安全**：增强 FilenameUtil.resolve() 方法，防止路径遍历攻击，处理 Windows/Linux 路径分隔符差异
- **投稿持久化**：投稿数据自动保存到 `_submissions_cache.json` 文件，插件重启后投稿数据不丢失
- **terminate 保存投稿**：`terminate()` 方法退出前自动保存投稿缓存，确保数据不丢失
- **auto_setup_blog 重构**：提取 `_do_auto_setup()` 方法，消除重复代码（检查资源→安装依赖→构建）

### 改进

- 投稿提交、批准、拒绝操作后自动持久化保存
- 权限错误消息更加详细，显示当前用户 ID 便于调试
- SSH 连接关闭时有异常处理，避免 terminate 本身失败

---

## [1.3.0] - 2026-06-23

### 新增

- **权限控制功能**：新增 `allow_only_owner` 配置项，开启后仅主人用户可使用插件
- **自定义主人 ID**：新增 `owner_user_id` 配置项，支持自定义主人用户 ID
- **投稿功能**：新增 `submit_post_draft` 工具，任何人都可以提交文章草稿
- **投稿列表**：新增 `list_post_submissions` 工具，查看所有待审核投稿
- **投稿详情**：新增 `review_submission` 工具，查看指定投稿的详细内容
- **批准投稿**：新增 `approve_submission` 工具，批准投稿并发布到博客
- **拒绝投稿**：新增 `reject_submission` 工具，拒绝投稿并可填写拒绝原因
- **显式指令**：新增用户可直接使用的命令，包括 `博客列表`、`博客搜索`、`博客环境`、`博客构建`、`博客部署`、`博客投稿列表`、`内存状态`

### 改进

- 在所有 LLM 工具方法中添加了权限检查逻辑
- 投稿功能无需权限验证，任何人都可以提交
- 投稿审核功能需要主人权限
- 投稿状态管理：支持待审核、已批准、已拒绝三种状态

---

## [1.2.0] - 2026-06-23

### 新增

- **内存资源提醒**：在 README 中添加了构建内存需求说明（约 1.5GB）及缓解方案
- **构建内存阈值配置**：新增 `build_memory_threshold` 配置项（默认 1536MB），可用内存低于此值时自动跳过构建
- **构建内存限制配置**：新增 `build_memory_limit` 配置项，可设置构建内存上限
- **并发构建控制**：新增 `allow_build_concurrent` 配置项，默认禁止并发构建避免内存竞争
- **内存状态检查工具**：新增 `check_memory_status` LLM 工具，查看当前内存状态和构建条件
- **构建资源检查工具**：新增 `check_build_resource` LLM 工具，检查磁盘空间和内存是否充足
- **构建配置查看工具**：新增 `get_build_config` LLM 工具，查看当前构建相关配置

### 改进

- 优化 `build_blog` 方法，构建前自动检查内存是否满足阈值要求
- 更新 `_check_system_resources` 方法，使用配置的内存阈值进行判断
- 优化内存不足时的提示信息，提供更详细的资源信息和解决方案

---

## [1.1.1] - 2026-06-23

### 修复

- **日期格式修复**：修复 YAML Front-matter 中日期字段被加引号的问题。之前 `published` 和 `updated` 字段输出为 `published: '2026-06-23'`（字符串类型），现在正确输出为 `published: 2026-06-23`（纯日期类型），符合 Astro 框架的要求。

### 改进

- 修复 `main.py` 中 `@register` 装饰器的版本号与 `metadata.yaml` 不一致的问题

---

## [1.1.0] - 2026-06-22

### 新增

- **资源监控警告**：构建前检测 CPU/内存使用率，超过阈值时显示警告并询问是否继续
- **构建过程资源日志**：后台记录构建期间的资源使用情况到 `build_resource_monitor.log`
- **Nginx/Apache 自动配置**：一键部署脚本支持自动配置 Web 服务器
- **SSL 证书配置**：支持 HTTPS 配置，自动生成 HTTP 到 HTTPS 重定向
- **域名配置**：支持自定义博客域名配置

### 修复

- **日期格式修复**：修复 `published` 和 `updated` 字段被写成字符串格式的问题，现在正确输出为 YAML 日期类型
- 修复自动检测机制未搜索 `/root` 用户目录的问题
- 修复克隆博客仓库时目标目录已存在导致失败的问题
- 修复 `BuildDeployManager` 跨类调用私有方法导致 `AttributeError` 的问题
- 修正默认部署路径配置为 `/var/www/firefly`

### 改进

- 增强智能检测和路径匹配（支持大小写不敏感）
- 提供更详细的错误信息和诊断提示
- 部署传输优先 rsync，失败自动回退到 scp

---

## [1.0.0] - 2026-06-22

### 新增

- 三种部署模式：`local_build`、`remote_build`、`local_only`
- 远程 SSH 支持，基于 asyncssh，带 keepalive 和自动重连
- 构建部署工具：环境检查、依赖安装、构建、部署
- 一键部署脚本支持 Web 服务器配置（Nginx/Apache）
- SSL 证书配置支持
- 域名配置支持
- 部署传输优先 rsync，失败自动回退到 scp

### 修复

- 修复自动检测机制未搜索 `/root` 用户目录的问题
- 修复克隆博客仓库时目标目录已存在导致失败的问题
- 修复 `BuildDeployManager` 跨类调用私有方法导致 `AttributeError` 的问题
- 修正默认部署路径配置为 `/var/www/firefly`

### 改进

- 增强智能检测和路径匹配
- 提供更详细的错误信息和诊断提示

---

## [0.x.x] - 早期版本

### 重构

- 从同步架构迁移到全异步架构
- 引入 `CommandExecutor` / `FileSystem` 抽象层，统一本地与远程操作接口
- 新增 `BuildDeployManager`，将构建和部署流程独立出来

### 新增

- 三种部署模式：`local_build`、`remote_build`、`local_only`
- 远程 SSH 支持，基于 asyncssh，带 keepalive 和自动重连
- 构建部署工具：`check_blog_environment`、`install_blog_dependencies`、`build_blog`、`deploy_blog`、`build_and_deploy_blog`
- 一键部署脚本 `deploy.sh`（Linux/macOS）和 `deploy.ps1`（Windows）
- 部署脚本自动检测并安装缺失依赖（Python、Node.js、pnpm、pip 包）
- 部署脚本自动克隆 Firefly 博客仓库（支持镜像加速）
- 部署传输优先 rsync，失败自动回退到 scp

### 变更

- 所有 LLM 工具返回值改为 `MessageEventResult`，使用 `yield` 输出结果
- 配置类型改为 `AstrBotConfig`，符合 AstrBot 插件规范
- `_conf_schema.json` 改为扁平 key-value 格式，使用 `options` 替代 `enum`
- 添加 `@register` 装饰器注册插件

### 修复

- 修复静态博客修改后未重新构建部署的问题
- 修复 SSH 断联后无法恢复操作的问题
- 修复构建时内存不足导致进程被杀的问题（通过 remote_build 模式）
- 修复 Windows 上远程路径分隔符兼容性问题

---

## [1.0.0] - 早期版本

- 基础文章管理功能（创建、删除、列出、查看、更新、搜索）
