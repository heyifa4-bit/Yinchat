# 明阴全自动小说

一个**本地、插件化**的 AI 小说创作软件。主体只负责"收发改拦"，所有能力（会话历史、知识库、背景注入、章纲、内容审核、逐章更新）都由**插件**实现；内置本地 embedding（bge-m3），无需联网即可完成语义检索。

> 完全脱离 AstrBot 独立运行，无内置内容安全审核，输出仅受可选的内容审核插件控制。

---

## ✨ 功能特性

- **插件化内核**：主体极简（收/发/改/拦），能力全部由插件提供；新插件放入 `plugins/` 后一键重载即可生效，设置命令表自动聚合
- **本地 embedding**：内嵌 bge-m3 模型，OpenAI 兼容 `/v1/embeddings`，随软件启动自动加载，无需额外服务
- **嵌套知识库**：主知识库（判别）→ embedding 知识库（向量检索）+ sqlite 库（主键检索）+ 固定文本（始终注入）
- **多 API 联合**：主对话模型 / 功能 API×2 / 大纲 API×2 可自由分工（审核、判别、章纲、卷纲分流）
- **单章特有指示**：`#once N` 标记独立成行，内容完整传给 AI 但**不进入长期记忆**
- **内容审核**：可选，输出与输入对比，冲突时回复首行打标签
- **桌面窗口**：启动小窗（加载模型）→ 自动切换主窗口；发送键在进行中变为"停止"可强制中断
- **对话管理**：多会话、重发/重试/删除/修改消息、单对话设置、温度调节

---

## 🚀 快速开始（exe 版）

```
dist\novel_app\
├── novel_app.exe          # 主程序
├── models\bge-m3-local\   # embedding 模型（4.25GB，与 exe 同级，不可缺失）
├── _internal\             # 运行库（torch 等，随包）
└── data\                  # 运行时数据（首次启动自动创建）
```

1. 双击 `novel_app.exe`
2. 出现"正在启动 embedding…"小窗口（首次加载约 10~60 秒，GPU 机器 6~9 秒）
3. 模型加载完成后自动进入主窗口
4. 左侧"模型设置"填写 **主对话模型**（写小说的 AI）与功能 API，即可开始

> 分享给他人时：将 `exe + models + _internal` 整体拷贝，**删除 `data\`**（含个人 API Key / 对话 / 知识库），接收方首次运行自动重建。

---

## 💻 源码运行

```bash
pip install -r requirements.txt

python main.py              # 桌面版（小窗 → 主窗）
python main.py --web        # 仅启动服务并在浏览器打开
python main.py --port 8000  # 指定端口
```

- 源码模式下模型从项目根 `models\bge-m3-local\` 读取
- 数据默认存于项目根 `data\`，可用环境变量 `NOVEL_DATA` 指定
- 端口可用环境变量 `NOVEL_PORT` 指定

---

## ⚙️ 模型配置（设置 → 模型设置）

| 配置 | 用途 | 必填 |
| --- | --- | --- |
| **主对话模型** | 最终生成端（写小说的 AI） | ✅（未配置时回退到功能 API 1） |
| 功能 API 1 | 剧情总结 / 知识库更新 / llm 判别 / 卷纲总结 | ✅ |
| 功能 API 2 | 多功能 API 模式下审核 / 判别分流 | 按需 |
| 大纲 API 1 | 卷纲生成、多 API 联合时章纲 | 按需 |
| 大纲 API 2 | 多大纲 API 模式 | 按需 |

- 每个 API 可配置：`base_url`、`api_key`、`model`（多个模型用半角逗号分隔）
- **base_url 自动补全**：默认自动在地址后补 `/v1/chat/completions`，可在模型设置页关闭该选项

---

## 💬 对话命令（消息以 `//` 开头，不进 LLM）

| 命令 | 说明 |
| --- | --- |
| `//help` | 显示帮助 |
| `//status` | 查看当前状态、插件列表与会话设置 |
| `//temp <0~2>` | 设置本对话温度 |
| `//name <名称>` | 设置本对话显示名 |
| `//kb list` | 列出主知识库 |
| `//kb create <名称> <描述>` | 创建主知识库 |
| `//kb add <名称> <内容>` | 向知识库添加内容 |
| `//kb set <名称> <内容>` | 覆盖知识库内容 |
| `//kb remove <名称>` | 删除知识库 |
| `//sqlite create <库名>` | 创建 sqlite 库 |
| `//sqlite table <库名> <表名> <字段:类型,...>` | 建表 |
| `//sqlite insert <库名> <表名> <json>` | 插入记录 |
| `//sqlite update <库名> <表名> <主键> <json>` | 更新记录 |
| `//sqlite delete <库名> <表名> <主键>` | 删除记录 |
| `//sqlite show <库名> [表名]` | 查看记录 |
| `//fixed <writing\|outline\|modify> <内容>` | 设置固定文本（写作要求/当前卷纲/修改要求） |
| `//kbset <套名>` | 切换 / 创建知识库套（本对话） |
| `//kbset-list` | 列出所有知识库套 |
| `//update` | 命令更新：总结剧情 → 判定 → 自动写入知识库 |
| `//outline update [补充要求]` | 生成卷纲并更新当前卷纲固定文本（可附要求，如标题、章节数，支持换行） |
| `//export` | 导出本对话到 txt |
| `//export-kb` | 导出全部知识库（含 sqlite / 固定文本 / embedding）到 txt |

---

## 🎛️ 会话设置（单对话设置面板）

| 设置 | 选项 | 默认 | 说明 |
| --- | --- | --- | --- |
| 知识库判别模式 | embedding / llm | embedding | embedding=内嵌向量判别；llm=功能 API 判别 |
| 每章检索全部知识库 | 开关 | 关 | 跳过主知识库判别，检索所有知识库 |
| 多功能 API 模式 | 开关 | 关 | 审核 / 判别使用功能 API 2 |
| 多大纲 API 模式 | 开关 | 关 | 章纲使用大纲 API 2 |
| 多 API 联合 | 开关 | 关 | 章纲由大纲 API 生成 |
| 内容审核 | 开关 | 关 | 输入输出对比，冲突时回复首行打标签 |
| 自动更新模式 | off / per_chapter / command | command | off=关闭；per_chapter=逐章后台更新；command=命令触发 |
| 知识库套 | 文本 | default | 当前对话使用的知识库套 |
| 注入上文条数 | 数字 | 20 | 置入对话首段的历史消息条数 |
| 对话名 / 温度 | - | - | 界面直接修改 |

---

## 🧠 知识库结构（每个"知识库套"独立一个文件夹）

```
data\kbs\<套名>\
├── master\            # 主知识库（判别用，决定注入哪些知识库）
├── embed\             # embedding 知识库（向量检索）
├── sqlite\            # sqlite 库（主键检索，如角色表/历史线表）
├── fixed\
│   ├── writing.txt    # 写作要求（始终注入）
│   ├── outline.txt    # 当前卷纲（始终注入）
│   └── modify.txt     # 修改要求（始终注入）
└── export\            # 导出文件输出目录
```

检索链路：主知识库判别 → 判定命中的知识库 → embedding 向量检索 + sqlite 主键检索 + 固定文本合并 → 注入 AI。

### 单章特有指示 `#once N`

在消息中**独立成行**写入：

```
#once 5
本章主角必须失忆
并且不能用第三人称
```

- 标记行 + 其后 5 行会**完整传给 AI**（作为本章临时指示）
- 但触发知识库更新（`//update` / 逐章更新 / 剧情总结）时，该标记及其后内容会被**自动剥离**，不会进入长期记忆
- 想让内容长期保留，就不加标记

---

## 🔌 插件系统

```
plugins\
├── plugin_table.json        # 插件表（重载时自动生成）
├── cmd_dispatch\plugin.py   # // 命令分发（置空不进 LLM）
├── history\plugin.py        # 会话历史注入（##### / #USER: / #AI: 格式）
└── novel\plugin.py          # 知识库 / 背景注入 / 章纲 / 审核 / 逐章更新
```

- 新插件放入 `plugins\<名称>\plugin.py`，定义 `Plugin` 子类并实现钩子
- 界面"插件设置"或 `POST /api/plugins/reload` 一键重载 → 插件表与设置命令表自动更新
- 插件可按 `priority` 排序，实现 `before_generate` / `after_generate` / `handle_command`

---

## 📁 数据与分享

| 内容 | 位置 | 说明 |
| --- | --- | --- |
| 对话 | `data\chats\` | JSON，按会话存储 |
| 知识库 | `data\kbs\` | 每套一个文件夹 |
| 模型配置 | `data\config.json` | **含 API Key，分享前务必删除** |
| 运行日志 | `data\runtime.log` | 崩溃排查 |

**发布分享**：只发 `exe + _internal + models` 三件套（约 8GB），接收方首次运行自动创建空的 `data\`，再自行填写 API Key。切勿直接分享带 `data\` 的整个目录。

---

## ❓ 常见问题

- **提示"处理失败: ConnectError"**：检查对应 API 的 base_url / api_key 是否正确、网络是否可达；模型设置页可关闭 base_url 自动补全
- **`//update` 提示"未获得有效 summary"**：功能 API 未正确返回 JSON summary 字段，检查功能 API 配置或模型是否支持
- **启动一直停在"正在启动 embedding…"**：确认 `models\bge-m3-local\` 与 exe 同级存在（12 个文件，4.25GB）
- **发送键不停止**：生成进行中按钮会变为"停止"，点击即强制取消当前任务（不保存半成品）
- **想要更快启动**：安装 CUDA 版 torch 并使用 NVIDIA GPU（RTX 系列），首次加载从约 2 分钟降到 6~9 秒

---

## 🔧 开发构建

```bash
# 单元测试
python -m unittest discover -s tests -v

# 打包 exe（约 8~10 分钟，产物在 dist\novel_app\）
build_exe.bat
```

| 版本 | 说明 |
| --- | --- |
| 1.0.0 | 独立软件首发：插件化内核、本地 embedding、桌面窗口、停止生成 |
