# HALF MVP 技术实现规格

**适用范围：** v0.x early open-source MVP
**配套文档：** `prd.md`（需求文档）

---

## 一、技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 后端 | Python 3.12 + FastAPI | 项目管理、Prompt 生成、状态推进、Git 轮询 |
| 数据库 | SQLite | 系统状态存储，轻量零运维 |
| ORM | SQLModel 或 SQLAlchemy | 与 FastAPI 集成良好 |
| 前端 | React 18 + Vite + TypeScript | 纯 SPA，与后端完全分离 |
| 路由 | React Router | 前端页面路由 |
| DAG 可视化 | React Flow | 任务依赖展示与交互 |
| Git 集成 | 优先 git CLI，必要时引入 GitPython | clone / pull / 读取 / 轮询 |
| 部署 | Docker Compose | 适配 OpenCloudOS 9.4 + Docker 28.0.1 |

**约束：**
- 不引入重型状态管理框架
- 后台轮询采用进程内后台任务，首版优先选简单实现
- 本地开发/同机协作部署可额外挂载共享工作区目录；当仓库副本中未命中文件且共享工作区 `origin` 与项目仓库 remote 一致时，允许按相同相对路径回退读取共享工作区文件
- 多机器协作场景下，轮询需先 `git fetch origin`，必要时直接读取 `origin/HEAD` 指向分支上的文件快照，而不是只依赖本地工作树是否能 fast-forward
- `git fetch origin` 成功但 `git pull --ff-only` 因分叉失败时，该情况只作为后端诊断 warning 记录日志，不写入计划或任务的 `last_error`
- 结构化计划格式统一 JSON，不支持 YAML

---

## 二、文件路径与命名规范

| 文件 | 固定路径 | 说明 |
|------|----------|------|
| 规划主输出 | `<collaboration_dir>/plan-<plan_id>.json` | 每轮规划使用唯一文件名，避免读取到历史结果；若未配置协作目录，则退化为仓库根目录 `plan-<plan_id>.json` |
| 任务主输出 | `<collaboration_dir>/<任务码>/result.json` | 任务完成契约已固定为任务目录 + `result.json` 哨兵。所有真实产物先写入 `<collaboration_dir>/<任务码>/`，再最后原子提交 `result.json`。轮询只认该固定路径，不再依赖 `expected_output_path` 推导真实结果文件 |
| 可选用量上报 | `<collaboration_dir>/<任务码>/usage.json` | 若任务需要上报用量，则与 `result.json` 放在同一任务目录内 |
| 附加辅助文件 | 任务输出目录下任意文件 | 系统只将 `result.json` 作为默认结果入口 |

### 路径处理约束（防误用）

所有仓库内路径在系统中**统一采用仓库根相对路径**，不得以 `/` 开头：

- `projects.collaboration_dir` 在创建/更新接口入口处由后端 strip 前导和尾部斜杠
- `project_plans.source_path` 和 `tasks.expected_output_path` 同样以仓库根相对路径存储
- 计划 finalize / 任务编辑时仍统一调用 `services.path_service.resolve_expected_output_path(..., strict=True)` 处理展示字段；成功后仅保存其 `normalized_path`
- 轮询检测与任务 Prompt 不再依赖 `expected_output_path` 推导结果文件路径，而是统一使用固定任务目录契约；前序任务展示也统一展示固定任务目录
- 若 `expected_output` 带前导斜杠、不含 `collaboration_dir` 前缀，或路径后附自然语言说明，由同一归一化函数提取并归一化；若无法唯一解析、疑似动作短语、越界或绝对路径，则直接报错
- `git_service.read_file` / `file_exists` 在 `os.path.join` 前再 strip 一次，作为最后一道防线

> 背景：Python `os.path.join(repo_dir, "/v2/plan.json")` 会把第二个参数视为绝对路径并丢弃 `repo_dir`，导致轮询去文件系统根目录查找文件而永远找不到。多层防御确保即使早期数据库存在脏数据（如 `collaboration_dir = "/v2"`）也能正确解析。

---

## 三、数据库 Schema

### `users`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| username | TEXT | 用户名 |
| password_hash | TEXT | 密码哈希 |
| role | TEXT | 用户角色：`admin` / `user`，默认 `user` |
| status | TEXT | 用户状态：`active` / `frozen`，默认 `active` |
| last_login_at | DATETIME | 最后一次登录时间 |
| last_login_ip | TEXT | 最后一次登录 IP |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

说明：

1. 现阶段系统区分管理员和普通用户。
2. 默认 `admin` 账号仅在不存在时自动创建；升级迁移时每次启动都会将 `username = 'admin'` 的用户 `role` 设为 `admin`，确保管理员身份不会因迁移顺序问题丢失。其余 `role` 为空的历史用户再统一回填为 `user`。
3. 冻结用户在 `get_current_user` 层统一返回 `403`，因此旧 token 也会立即失效。

### `audit_logs`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| operator_id | INTEGER FK | 操作者，关联 `users.id` |
| action | TEXT | 操作类型，例如 `user.password.change` / `user.role.update` / `user.status.update` |
| target_type | TEXT | 目标类型，例如 `user` |
| target_id | INTEGER | 目标对象 ID |
| detail | TEXT | 结构化详情（JSON 字符串） |
| created_at | DATETIME | 操作时间 |

约束：

1. `detail` 中不得出现密码明文或密码哈希。
2. 密码修改的 detail 当前仅记录 `{"user_id": N}`。
3. 管理员角色变更与状态变更应记录 old/new 值，便于追溯。

### 通用时间序列化与展示

项目、计划、任务、任务事件、用户和审计日志中的运行事件时间按 UTC 存储和传输。由于默认 SQLite / SQLAlchemy 组合读回时可能得到 naive datetime，API 响应层必须统一将这些 naive datetime 视为 UTC 并序列化为带明确 UTC 标记的字符串（如 `Z` 或 `+00:00`）；不得依赖 `DateTime(timezone=True)` 作为 SQLite 下的唯一修复手段。

前端对上述运行事件时间统一使用共享日期工具格式化，按浏览器本地时区展示，不在 UI 中额外追加 `UTC+8`、`Asia/Shanghai` 等时区标识。

当前范围不包含 Agent 短期/长期重置时间；该部分仍按下文 Agent 重置时间规则处理，并将在后续独立改造中统一到浏览器本地时区。

### `global_settings`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| key | TEXT UNIQUE | 设置键名，例如 `polling_interval_min`、`polling_interval_max`、`polling_start_delay_minutes`、`polling_start_delay_seconds`、`task_timeout_minutes`、`plan_co_location_guidance` |
| value | TEXT | 设置值；数字类配置以字符串保存，`plan_co_location_guidance` 以纯文本保存 |
| description | TEXT | 设置描述 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

存储系统全局默认参数。当项目创建时若未指定对应项目参数，则读取全局默认值；当项目指定了对应参数，则使用项目级值。全局项目参数包括轮询间隔、轮询启动延迟和默认 Task 超时时间。规划 Prompt 的同机分配引导也存储在该表，key 为 `plan_co_location_guidance`；未保存或读取到空白值时回退到后端默认引导文案。

### `agents`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| name | TEXT | Agent 显示名称 |
| slug | TEXT UNIQUE | 唯一标识，用于结构化计划中的 assignee |
| agent_type | TEXT | Agent 类型（如 claude-code, codex） |
| model_name | TEXT | 主模型名称，兼容旧字段，默认取 `models_json` 中第一项 |
| models_json | TEXT | Agent 模型配置列表 JSON，每项包含 `model_name` 和 `capability` |
| capability | TEXT | 主能力摘要，兼容旧字段，默认由 `models_json` 汇总得到 |
| co_located | BOOLEAN | 是否默认与项目部署机器同服务器，用作项目级 Agent 绑定时的默认值 |
| is_active | BOOLEAN | 是否启用 |
| availability_status | TEXT | 可用状态：available / short_reset_pending / long_reset_pending（不可用状态由 subscription_expires_at 实时推导，不存储） |
| display_order | INTEGER | 手动排序序号，默认 0；拖拽排序后更新，自动排序时重置 |
| subscription_expires_at | DATETIME | 订阅到期时间 |
| short_term_reset_at | DATETIME | 短期重置时间，统一按北京时间本地时间存储，不附带 UTC 偏移 |
| short_term_reset_interval_hours | INTEGER | 短期重置间隔，单位小时 |
| short_term_reset_needs_confirmation | BOOLEAN | 短期重置已自动续推后，是否等待用户确认/重置 |
| long_term_reset_at | DATETIME | 长期重置时间，统一按北京时间本地时间存储，不附带 UTC 偏移 |
| long_term_reset_interval_days | INTEGER | 长期重置间隔，单位天（仅 days 模式使用） |
| long_term_reset_mode | TEXT | 长期重置模式：days（按天间隔）/ monthly（每月同日同时刻，日期和时间由 long_term_reset_at 决定），默认 days |
| long_term_reset_needs_confirmation | BOOLEAN | 长期重置已自动续推后，是否等待用户确认/重置 |
| created_by | INTEGER FK | 创建者（关联 users.id），作为智能体 owner 字段。`/api/agents` 全链路按该字段隔离；历史 `NULL` 值会在启动阶段自动回填到默认管理员账号 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

补充约束：

1. `PUT /api/agents/{id}` 采用**部分更新语义**。当请求只携带 `capability`、`co_located`、`name` 或其他局部字段时，后端只更新这些显式提交的字段，不得把 `model_name` / `models_json` 误清空。
2. 只有在请求中显式携带 `models` 时，后端才重建 `models_json`，并由其重新推导顶层 `model_name` 与 `capability`。
3. 删除智能体时，任务/项目引用检查只在当前用户自己的项目域内执行；其他用户项目中的历史脏引用不应阻止当前用户删除自己的智能体。

### agent_type_configs（Agent 类型配置）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| name | TEXT UNIQUE | Agent 类型名称 |
| description | TEXT | Agent 介绍，描述该类型智能体的能力、使用限制等说明，用于任务匹配时参考 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### model_definitions（模型定义）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| name | TEXT UNIQUE | 模型名称 |
| alias | TEXT | 模型别名（可选），用于模型身份识别 |
| capability | TEXT | 能力描述（≤150 字），在项目执行时作为模型选择参考 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### agent_type_model_map（Agent 类型-模型映射）

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| agent_type_id | INTEGER FK | 关联 agent_type_configs.id |
| model_definition_id | INTEGER FK | 关联 model_definitions.id |
| UNIQUE | (agent_type_id, model_definition_id) | 联合唯一约束 |

模型身份识别规则：当输入的模型名称或别名与已有模型的名称或别名匹配时，识别为同一模型并共享能力描述字段。

重置时间处理约束：

- 创建/编辑 Agent 时，前端先按用户选择的源时区将输入时间换算为北京时间年月日时分，再提交给后端。
- 后端收到 `short_term_reset_at`、`long_term_reset_at` 后，统一规范为“北京时间无时区 datetime”，入库时不得再转换为 UTC。
- 列表展示和编辑回填时，前端按存储的北京时间字段直接格式化，不再通过浏览器 `Date` 做额外时区推导。
- 自动续推逻辑以北京时间当前时刻为基准：短期按小时推进，长期按天推进。
- 当自动续推发生且对应时间、间隔都已设置时，后端把 `*_reset_needs_confirmation` 设为 `true`，前端在对应倒计时下展示“重置 / 确认”按钮。
- `重置` 操作按钮使用黄色底色以突出人工重置动作；点击后会把对应重置时间更新为”当前北京时间 + 对应间隔”，并把确认标记清除；`确认` 操作不修改时间，只清除确认标记。
- 点击长期重置的 `重置` 按钮时，若该 Agent 同时设置了短期重置时间和短期间隔，则一并执行短期重置（短期重置时间更新为”当前北京时间 + 短期间隔”，短期确认标记清除）。反之，短期重置不影响长期重置。
- 用户通过编辑接口手动修改对应重置时间或重置间隔后，后端立即清除对应确认标记，前端不再显示按钮。
- 为修复旧版本把北京时间误当作 UTC 存储的问题，启动时执行一次性数据迁移：已有 `short_term_reset_at`、`long_term_reset_at` 统一 `+8 hours` 后回写。

### `projects`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| name | TEXT | 项目名称 |
| goal | TEXT | 项目目标 / Plan 页任务介绍。Prompt 路径点击“生成 Prompt”前保存；模版路径点击“下一步”应用模版前保存。执行 Prompt 生成时读取该字段作为项目任务介绍 |
| git_repo_url | TEXT | Git 仓库地址 |
| collaboration_dir | TEXT | Git 仓库内协作目录，用于计划文件和任务协作文件定位。**必须为仓库根相对路径**，前导/尾部斜杠在创建/更新时由后端 strip。**项目创建时若用户未提供，系统自动生成 `outputs/proj-<项目id>-<随机串>` 作为默认值**，确保不同项目输出目录低碰撞、可区分。用户可显式覆盖为自定义路径 |
| status | TEXT | draft / planning / executing / completed / abandoned |
| agent_ids_json | TEXT | 项目参与 Agent 绑定 JSON，格式为 `[{ "id": 1, "co_located": true }]`；API 响应同时派生返回 `agent_ids` |
| polling_interval_min | INTEGER | 轮询间隔最小值（秒）。项目创建时若用户未显式填写，则后端读取当前全局默认值并快照写入项目记录 |
| polling_interval_max | INTEGER | 轮询间隔最大值（秒）。项目创建时若用户未显式填写，则后端读取当前全局默认值并快照写入项目记录 |
| polling_start_delay_minutes | INTEGER | 轮询启动延迟分钟数。项目创建时若用户未显式填写，则后端读取当前全局默认值并快照写入项目记录 |
| polling_start_delay_seconds | INTEGER | 轮询启动延迟秒数。项目创建时若用户未显式填写，则后端读取当前全局默认值并快照写入项目记录 |
| task_timeout_minutes | INTEGER | 项目级默认 Task 超时时间（分钟），范围 1-120。项目创建时若用户未显式填写，则后端读取当前全局默认值并快照写入项目记录；项目更新时显式传 `null` 表示重新写入当前全局默认值 |
| planning_mode | TEXT | Prompt 规划路径使用的规划模式，默认 `balanced`。合法值：`balanced`（均衡模式）、`quality`（效果优先）、`cost_effective`（性价比高）、`speed`（速度优先）。创建/更新时后端按白名单校验，空值/缺省回落到 `balanced`；前端项目创建/编辑页不展示该字段，Plan 页面在“由 Prompt 生成流程”路径下选择并在生成 Prompt 前保存；该字段用于规划 Prompt 的任务拆分、Agent/模型分配、并发和评审策略 |
| template_inputs_json | TEXT | Plan 页使用流程模版生成流程时保存的模版必需输入值，JSON 扁平对象 `{ key: value }`。项目更新接口只校验对象为扁平结构，key 为非空字符串，value 不允许为对象或数组；是否属于当前模版声明字段由前端按所选模版过滤，执行 Prompt 生成时再按来源模版声明二次过滤 |
| created_by | INTEGER FK | 创建者（关联 users.id），作为项目域 owner 字段。项目列表/详情/计划/任务/轮询等接口均按该字段做当前用户过滤；历史 `NULL` 值会在启动阶段自动回填到默认管理员账号 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### `project_plans`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| project_id | INTEGER FK | 所属项目 |
| source_agent_id | INTEGER FK | 生成该计划的 Agent |
| plan_type | TEXT | candidate / final |
| plan_json | TEXT | 结构化计划 JSON 原文 |
| prompt_text | TEXT | 本次发送给 Agent 的规划 Prompt |
| status | TEXT | pending / running / completed / needs_attention / final |
| source_path | TEXT | 计划文件轮询路径，Prompt 路径默认 `<collaboration_dir>/plan-<plan_id>.json`，**仓库根相对路径**，不得以 `/` 开头；流程模版路径记录为 `template:<template_id>`，轮询服务必须跳过这类来源 |
| include_usage | BOOLEAN | 是否要求 Agent 回写规划阶段用量信息 |
| selected_agent_ids_json | TEXT | 本轮参与规划的 Agent ID 列表 JSON |
| selected_agent_models_json | TEXT | 本轮参与规划的 Agent 模型选择结果 JSON，key 为 agent_id，value 为模型名；未显式选择时保存系统自动决策结果 |
| dispatched_at | DATETIME | Prompt 复制并派发时间 |
| detected_at | DATETIME | 轮询检测到合法 plan.json 的时间 |
| last_error | TEXT | 最近一次用户需关注的计划生成错误；可降级的 Git sync warning 不写入该字段 |
| is_selected | BOOLEAN | 是否被选为最终计划 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

一个项目可有多个候选计划，最终只保留一个 `final`。应用流程模版时，后端会先删除同项目下旧的未选中候选计划，再创建已完成的模版候选计划并立即定稿；既有最终计划语义由定稿流程负责替换。

### `process_templates`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| name | TEXT | 模版名称，最终保存值必须非空；请求未传或为空时从 `template_json.plan_name` 派生 |
| description | TEXT | 模版描述，允许为空；请求未传或为空时从 `template_json.description` 派生 |
| prompt_source_text | TEXT | 用户在流程模版页“输入描述”中填写的详细流程描述，用于生成模版编写 Prompt；允许为空，独立于 `description`、`template_json` 和项目 `goal` |
| agent_count | INTEGER | 模版需要的抽象 Agent 槽位数量 |
| agent_slots_json | TEXT | 抽象角色槽位数组 JSON，例如 `["agent-1","agent-2"]` |
| template_json | TEXT | 流程模版 JSON 原文，任务结构与计划 JSON 一致，但 `assignee` 必须使用 `agent-N` 抽象槽位 |
| agent_roles_description_json | TEXT | 当前槽位的角色说明 JSON，key 为 `agent-N`，value 为职责和适合绑定的 Agent 类型说明；允许为空 |
| required_inputs_json | TEXT | 模版必需输入字段声明 JSON 数组。每项包含 `key`、`label`、`required`、`sensitive`；`key` 必须符合 `[a-zA-Z_][a-zA-Z0-9_]*` 且数组内唯一，`label` 非空，`required/sensitive` 必须为 boolean |
| created_by | INTEGER FK | 创建者（关联 users.id）；所有登录用户可查看/使用，创建者和管理员可编辑/删除 |
| updated_by | INTEGER FK | 最近更新者 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

模版 JSON 校验规则：

- 根节点必须是对象，且包含非空 `tasks` 数组。
- 每个 task 必须包含非空 `task_code`、`task_name`、`description`、`assignee`、`depends_on`。
- `task_code` 必须唯一；`assignee` 只能是 `agent-[1-9]\d*` 抽象槽位，禁止直接保存真实 Agent slug。
- `depends_on` 必须只引用已存在任务码，依赖图不得有环。
- `expected_output` 缺省时归一化为 `outputs/<task_code>/result.json`；显式提供时必须通过仓库根相对路径校验，拒绝绝对路径、越界路径和无法唯一解析的自然语言路径。
- JSON 顶层可包含 `agent_roles` 数组，每项包含 `slot` 和 `description`，供前端预填槽位角色说明；后端不把它作为槽位数量来源，槽位数量只来自 `tasks[].assignee`。

### `tasks`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| project_id | INTEGER FK | 所属项目 |
| plan_id | INTEGER FK | 来源计划 |
| task_code | TEXT UNIQUE | 任务码（如 TASK-001） |
| task_name | TEXT | 任务名称 |
| description | TEXT | 任务描述 |
| assignee_agent_id | INTEGER FK | 执行 Agent |
| status | TEXT | pending / running / completed / needs_attention / abandoned |
| depends_on_json | TEXT | 前置任务码 JSON 数组，如 `["TASK-001"]` |
| expected_output_path | TEXT | 预期输出说明字段，仍以仓库根相对路径形式持久化，主要用于展示、参考和保留规划阶段语义；当前版本的轮询检测与任务 Prompt 不再依赖它推导真实结果文件路径 |
| result_file_path | TEXT | 实际检测到的结果文件路径 |
| usage_file_path | TEXT | 实际检测到的用量文件路径 |
| last_error | TEXT | 最近一次用户需关注的错误信息；可降级的 Git sync warning 不写入该字段 |
| timeout_minutes | INTEGER | Task 级超时时间（分钟），范围 1-120。最终计划生成 Task 时从项目级默认值快照写入；`pending` 状态可编辑，派发后不可编辑；异常历史数据为空时轮询按项目级默认、全局默认、10 分钟顺序兜底 |
| dispatched_at | DATETIME | Prompt 复制时间（开始计时） |
| completed_at | DATETIME | 任务完成时间 |
| created_at | DATETIME | 创建时间 |
| updated_at | DATETIME | 更新时间 |

### `task_events`

| 字段 | 类型 | 说明 |
|------|------|------|
| id | INTEGER PK | 自增主键 |
| task_id | INTEGER FK | 所属任务 |
| event_type | TEXT | dispatched / completed / timeout / manual_complete / abandoned / redispatched / updated / error |
| detail | TEXT | 事件详情 |
| created_at | DATETIME | 事件时间 |

用于记录状态变更和人工介入操作，支撑执行汇总页展示。

补充约束：

1. `manual_complete` 触发时，任务状态更新为 `completed` 的同时必须清除 `last_error`。
2. 已经进入 `needs_attention` 的任务在后续轮询中若结果仍未出现，不应重复写入新的 `timeout` 事件；只有首次从 `running` 转入 `needs_attention` 时才写入 timeout 事件。

---

## 四、API 接口清单

### 认证

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/auth/config` | 返回认证页运行配置；当前用于暴露 `allow_register`，让前端决定是否展示注册入口 |
| POST | `/api/auth/register` | 用户注册，校验密码强度（大小写字母+数字，≥8位），新用户默认 `role=user`、`status=active`，返回 token / username / role / status |
| POST | `/api/auth/login` | 用户名+密码登录；冻结用户返回 `403`；登录成功后更新 `last_login_at` 与 `last_login_ip`，返回 token / username / role / status |
| GET | `/api/auth/me` | 获取当前用户信息（`id` / `username` / `role` / `status`），用于前端判断登录态与角色 |
| PUT | `/api/auth/password` | 当前登录用户修改自己的密码。校验当前密码、强度规则与“新旧密码不同”；业务错误统一返回 `400`，未认证返回 `401`；成功后写入 `user.password.change` 审计日志 |
| GET | `/health` | 健康检查端点，返回 `{"status": "ok"}` |

### Agent 管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agents` | 当前登录用户自己的 Agent 列表（含 `models` 数组、订阅到期、短期/长期重置时间及间隔、自动续推后的确认标记） |
| POST | `/api/agents` | 为当前登录用户创建 Agent，支持提交多个 `models[]` 配置项 |
| PUT | `/api/agents/:agentId` | 编辑当前登录用户自己的 Agent，支持更新多个 `models[]` 配置项 |
| POST | `/api/agents/:agentId/short-term-reset/reset` | 将短期重置时间重置为当前北京时间 + 短期间隔，并清除确认标记 |
| POST | `/api/agents/:agentId/short-term-reset/confirm` | 确认当前短期重置时间无误，清除确认标记 |
| POST | `/api/agents/:agentId/long-term-reset/reset` | 将长期重置时间重置为当前北京时间 + 长期间隔，并清除确认标记；同时若短期重置时间和间隔均已设置，则一并重置短期 |
| POST | `/api/agents/:agentId/long-term-reset/confirm` | 确认当前长期重置时间无误，清除确认标记 |
| PATCH | `/api/agents/:agentId/status` | 手动切换当前登录用户自己的 Agent 可用状态（available / short_reset_pending / long_reset_pending），订阅已过期时拒绝操作。该接口仅修改 `availability_status`，不调用 `_normalize_agent_input`，不影响模型、能力、重置策略等其他字段 |
| GET | `/api/agents/config/types` | 当前登录用户可读的 Agent 类型目录，只读返回类型与模型信息，用于普通用户创建/编辑 Agent 时选择类型和模型 |

说明：

- 创建 Agent 前在服务端增加显式防御性检查：若 `created_by` 理论上为空，则直接返回 `500`，禁止写入脏数据。

### 智能体设置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/agent-settings/types` | 获取所有 Agent 类型及其关联模型，仅管理员可用 |
| POST | `/api/agent-settings/types` | 创建 Agent 类型，仅管理员可用 |
| PUT | `/api/agent-settings/types/:typeId` | 修改 Agent 类型名称（同步更新已有 Agent 的 agent_type 字段），仅管理员可用 |
| DELETE | `/api/agent-settings/types/:typeId` | 删除 Agent 类型（该类型下有已创建 Agent 时拒绝），仅管理员可用 |
| POST | `/api/agent-settings/types/:typeId/models` | 向 Agent 类型添加模型（自动匹配已有模型身份），仅管理员可用 |
| DELETE | `/api/agent-settings/types/:typeId/models/:modelId` | 从 Agent 类型移除模型，仅管理员可用 |
| PUT | `/api/agent-settings/models/:modelId` | 修改模型定义（名称、别名、能力描述），能力描述变更全局生效，仅管理员可用 |
| GET | `/api/agent-settings/models/search?q=xxx` | 按名称或别名搜索模型，用于自动补全，仅管理员可用 |
| GET | `/api/agent-settings/types/search?q=xxx` | 按名称搜索 Agent 类型，用于自动补全，仅管理员可用 |

### 项目管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects` | 项目列表 |
| POST | `/api/projects` | 创建项目。`collaboration_dir` 可空，留空时系统在 flush 拿到自增 id 后自动设为 `outputs/proj-<id>-<random>`；请求体兼容 `planning_mode`，缺省为 `balanced`，但前端创建页不展示该字段。若请求中的 Agent 经状态派生后为 `unavailable`，接口返回 400，并在错误体中附带不可用 Agent id 列表 |
| GET | `/api/projects/:id` | 项目详情（含"下一步"提示和状态摘要），响应包含 `planning_mode` |
| PUT | `/api/projects/:id` | 编辑项目；支持更新 `goal`、`planning_mode` 和 `template_inputs`，非法值返回 400。Plan 页面 Prompt 路径生成 Prompt 前通过该接口保存当前任务介绍和规划模式；模版路径应用模版前保存 `{ goal, template_inputs }`，不得顺带写 `planning_mode`。`template_inputs` 只允许扁平对象，嵌套对象或数组返回 400。更新 Agent 绑定时以数据库中更新前的 `Project.agent_ids_json` 作为 `allow_keep_ids`：原已关联的 `unavailable` Agent 允许继续保留，任何新加入的 `unavailable` Agent 一律返回 400 |

### 工作计划

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/:id/plans/generate-prompt` | 生成计划 Prompt，保存为 `pending` 规划记录，不启动轮询；若同项目存在未派发、未检测、未选中、无 `plan_json` 的 candidate pending plan，则复用该记录并保持已有 `source_path` 不变，只更新 Prompt 输入和派生字段；请求体支持 `selected_agent_models`，可为每个已选 Agent 指定一个模型；后端从项目读取 Plan 页面刚保存的 `planning_mode` 并注入对应规划策略 |
| POST | `/api/projects/:id/plans/:planId/dispatch` | 在用户点击“拷贝 Prompt”后启动或恢复规划轮询 |
| GET | `/api/projects/:id/plans` | 获取候选计划和最终计划，返回 `selected_agent_models` |
| POST | `/api/projects/:id/plans/finalize` | 确认最终计划并解析为任务 DAG。解析前进行有限度 JSON 自动修复（去除 markdown 代码围栏、移除尾部逗号） |

### 流程模版

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/process-templates/generate-prompt` | 根据用户输入的适用场景 `scenario`（可空）和流程描述 `description`（必填）生成“让外部 Agent 编写流程模版 JSON”的 Prompt，不直接调用模型；Prompt 明确区分流程目标上下文和详细流程需求，并要求输出顶层 `agent_roles` 说明每个 slot 的职责和适合 Agent 类型 |
| GET | `/api/process-templates` | 获取所有流程模版列表；所有登录用户可见，响应包含 `can_edit`、归一化后的 `agent_roles_description` 和 `required_inputs` |
| POST | `/api/process-templates` | 创建流程模版。校验 JSON、抽取 `agent_slots` / `agent_count`，名称最终不能为空，描述允许为空；请求可携带 `prompt_source_text`、`agent_roles_description` 和 `required_inputs`。后端只保留当前槽位对应的非空角色说明字符串，并严格校验 required_inputs 结构 |
| GET | `/api/process-templates/:templateId` | 获取单个流程模版详情，返回 `prompt_source_text`、归一化后的 `agent_roles_description` 和 `required_inputs` |
| PUT | `/api/process-templates/:templateId` | 编辑流程模版；仅创建者或管理员可用。更新 JSON 后重新抽取槽位和元数据，空名称只能从 JSON `plan_name` 派生，仍为空则 400；`prompt_source_text` 未传时保留原值，传空字符串时清空，传非空字符串时覆盖；未传 `agent_roles_description` 时保留仍存在槽位的旧说明，传 `{}` 时清空说明；未传 `required_inputs` 时保留原值，传入时按创建规则严格校验并覆盖 |
| DELETE | `/api/process-templates/:templateId` | 删除流程模版；仅创建者或管理员可用。删除不影响已由该模版生成的项目任务 |
| POST | `/api/process-templates/:templateId/apply/:projectId` | 将模版应用到项目。项目状态必须为 `draft` 或 `planning`；槽位映射必须完整、无额外项、无重复 Agent，且映射 Agent 必须属于当前项目和当前用户。接口请求体只接收 `slot_agent_ids`，不接收任务介绍或 `template_inputs`；Plan 页面会在调用该接口前通过 `PUT /api/projects/:id` 保存 `{ goal, template_inputs }`。成功后创建并定稿计划，跳转任务执行阶段 |

### 任务管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/projects/:id/tasks` | 获取项目所有任务（含状态、依赖） |
| GET | `/api/tasks/:taskId` | 获取单个任务详情 |
| PUT | `/api/tasks/:taskId` | 更新当前可执行任务的名称、描述、预期输出，前端自动保存 |
| POST | `/api/tasks/:taskId/generate-prompt` | 生成执行 Prompt（含可选用量勾选参数）。若任务所属项目 `goal` 去除首尾空白后非空，则在身份句之后、`## 执行前置步骤` 之前插入 `## 项目任务介绍` 段；若 task 可通过 `task.plan_id -> project_plans.source_path = template:<id>` 追溯到流程模版，且项目 `template_inputs_json` 中存在该模版 `required_inputs_json` 声明字段的非空值，则在项目任务介绍之后、执行前置步骤之前插入 `## 模版所需信息` 段；无法追溯、模版不存在、字段为空或 JSON 非法时整段省略 |
| POST | `/api/tasks/:taskId/dispatch` | 标记 Prompt 已复制，开始计时；请求体支持 `ignore_missing_predecessor_outputs`，仅当用户显式选择“继续执行（忽略前序输出）”时为 true |
| POST | `/api/tasks/:taskId/mark-complete` | 手动标记完成 |
| POST | `/api/tasks/:taskId/abandon` | 标记放弃 |
| POST | `/api/tasks/:taskId/redispatch` | 重新派发；前端调用前会先 `generate-prompt` 并复制到剪贴板，再调本接口；请求体支持 `ignore_missing_predecessor_outputs`，规则同 dispatch；服务端将原 `last_error` 归档至 `redispatched` 事件 detail 后清空 |
| GET | `/api/tasks/:taskId/predecessor-status` | 兼容保留接口：返回 HALF 后端当前可见仓库视图中的前序输出观测结果，不再作为页面派发流程的一部分 |
| GET | `/api/projects/:id/predecessor-status` | 兼容保留接口：返回项目下各任务的前序输出观测结果，当前页面流程不再消费 |

派发流程的完整规则：

- 派发或重新派发任务前，前端只校验前序任务的状态字段（必须全部为 `completed` 或 `abandoned`），不再调用 `predecessor-status` 检查前序输出文件是否存在。
- 「派发」与「重新派发」共用同一条前端编排链路 `performDispatch`，并强制采用「先预取、后同步写入剪贴板」的顺序，原因见下方 *剪贴板 user activation 约束*：
  1. **预取 Prompt**：`TaskDetailPanel` 在选中或刷新一个处于 `pending / needs_attention / running` 的任务时，立即在后台 `POST /api/tasks/:taskId/generate-prompt` 把最新 Prompt 缓存到组件 state；任务的 `task_name / description / expected_output_path / status` 任一变化都会重新拉取一次。若当前任务仍有本地草稿未保存或刚保存但列表数据尚未回刷，旧的 `cachedPrompt` 必须立刻失效，按钮改为 *Prompt 准备中...* 且保持 disabled，直到自动保存完成并按最新服务端字段重新预取成功，避免用户把上一版描述派发出去。
  2. **同步写剪贴板**：用户点击按钮时，`performDispatch` 在任何 `await` 之前**直接调用** `copyText(cachedPrompt, navigator.clipboard)`。`copyText` 内部第一行就同步触发 `clipboard.writeText(...)`，因此浏览器在判定 user activation 时仍处于点击的同步执行栈内。复制成功后立刻把按钮 label 切换为「Prompt 已复制」。
  3. **失败必须显式中止**：若 `copyText` 抛错或返回 `false`（包括剪贴板权限被拒、`writeText` 因 activation 失效而失败、`document.execCommand('copy')` fallback 也失败等任何情况），`performDispatch` 必须 `alert` 提示用户并立即返回，**不得**继续调用 `/dispatch` 或 `/redispatch` 落库；保证「按钮显示已复制」与「剪贴板里真有该 Prompt」「DB 记录为已派发」三者同进同退，杜绝出现"剪贴板里残留前一个任务 Prompt 但 UI 显示派发成功"的错觉。
  4. **派发落库**：复制确认成功后，再调用 `POST /api/tasks/:taskId/dispatch` 或 `/redispatch` 写一条 DB 记录。该接口纯 DB 写，不触发任何远端 IO。
  两条接口的差异仅在事件类型 (`dispatched` vs `redispatched`) 与允许的入口状态 (`pending|needs_attention` vs `running|needs_attention`)。

- **剪贴板 user activation 约束**：现代浏览器要求 `navigator.clipboard.writeText` 调用时仍处于 transient user activation 期间（点击触发的同步执行栈或紧邻的微任务）。如果在点击之后先 `await` 一次网络请求再调用 `writeText`，activation 已被消耗，`writeText` 会被静默拒绝；旧的 `document.execCommand('copy')` fallback 在同样场景下也会失败。这正是历史 bug *task 切换后剪贴板里仍是上一个 task 的 Prompt* 的根因，因此 `performDispatch` 必须采用上述「预取 + 同步写入」流程，禁止在点击与剪贴板写入之间夹任何 `await`。
- 服务端 `dispatch / redispatch` 不触发 `git fetch / git pull`，也不校验前序任务输出文件是否已经存在于本地仓库。理由：HALF 后端只是任务调度面，真正执行任务的 Agent 通常运行在另一台机器上，部署 HALF 的服务器去拉远端仓库、或替 Agent 判断“前序输出是否可读”，都不具备可靠语义；真正有效的检查只能发生在 Agent 自己的执行环境中。
- `predecessor-status` 接口保留为兼容与诊断用途，但不再参与任务页面的红底标记、派发阻塞或确认弹窗。
- 若前序任务状态为 `abandoned`，后继任务视为已解除阻塞，不要求该前序任务输出文件存在。
- 系统下发给 Agent 的任务 Prompt 在最前面强制要求 Agent 先在项目仓库执行 `git pull`，并检查 Prompt 中列出的前序任务输出是否真实存在；若缺失，应停止执行并与项目负责人沟通。

### 状态与汇总

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/projects/:id/poll` | 手动触发一次 Git 轮询刷新 |
| GET | `/api/projects/:id/polling-config` | 获取项目的有效轮询配置和默认 Task 超时时间（项目级覆盖全局默认） |
| GET | `/api/projects/:id/summary` | 获取执行汇总数据 |

### 全局设置

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/settings/polling` | 获取全局项目参数默认值（polling_interval_min, polling_interval_max, polling_start_delay_minutes, polling_start_delay_seconds, task_timeout_minutes） |
| PUT | `/api/settings/polling` | 更新全局项目参数默认值，仅管理员可用；普通用户保留只读能力以支持项目创建表单读取默认值；`task_timeout_minutes` 校验范围为 1-120 分钟 |
| GET | `/api/settings/prompt` | 获取全局 Prompt 设置；登录用户可读，返回 `co_location_guidance` 和 `default_co_location_guidance` |
| PUT | `/api/settings/prompt` | 更新全局 Prompt 设置，仅管理员可用；`co_location_guidance` 必须为非空字符串，空白值返回 400 |

### 用户管理

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/admin/users` | 获取系统用户列表，仅管理员可用；返回 `id` / `username` / `role` / `status` / `created_at` / `last_login_at` / `last_login_ip` |
| PUT | `/api/admin/users/:userId/role` | 修改用户角色，仅管理员可用；禁止修改自己的角色，且不得让最后一个激活管理员失效 |
| PUT | `/api/admin/users/:userId/status` | 修改用户状态（冻结 / 解冻），仅管理员可用；禁止冻结自己，且不得冻结最后一个激活管理员 |
| GET | `/api/admin/audit-logs` | 查询操作审计日志，仅管理员可用；支持 `action` 过滤与 `limit`（最大 200），返回 `operator_username` |

补充说明：

- `PUT /api/admin/users/:userId/role` 成功后写入 `user.role.update` 审计日志
- `PUT /api/admin/users/:userId/status` 成功后写入 `user.status.update` 审计日志

---

## 五、页面路由与交互说明

### 页面清单

左侧导航固定包含 `/projects`（项目）、`/agents`（智能体）与 `/templates`（流程模版）；当当前用户角色为管理员时，额外展示 `/admin/users`（用户管理）。左侧导航底部展示”欢迎您，{username}”文字（位于”退出登录”按钮上方），`username / role / status` 在登录成功后写入本地存储，并在布局初始化时通过 `/api/auth/me` 同步刷新；退出时一并清除。

左侧导航底部同时提供“修改密码”按钮。点击后打开模态弹窗，包含当前密码、新密码、确认新密码三个字段：

- 确认密码不一致时，前端直接拦截并提示
- 当前密码错误、新旧密码相同等后端 `400` 业务错误，前端必须透传并展示明确文案
- 这些业务错误不会触发全局 `401` 登出逻辑，用户仍保留当前登录态
- 修改成功后展示成功提示，并自动关闭弹窗

| 路径 | 页面 | 核心功能 |
|------|------|----------|
| `/login` | 登录/注册页 | 页面加载时先请求 `/api/auth/config`；当 `allow_register=true` 时展示登录/注册切换并校验注册密码强度，当 `allow_register=false` 时隐藏注册入口并提示当前环境未开放自助注册 |
| `/projects` | 项目列表页 | 项目卡片列表，展示名称、状态、Agent 数量、创建时间；右上角"创建项目"入口；"设置"入口仅管理员可见 |
| `/projects/new` | 项目创建页 | 填写名称、目标、Git 仓库地址，选择参与 Agent；Agent 卡片展示统一状态和全部模型名称；进入页面时自动预填当前全局轮询默认值和默认 Task 超时时间，用户可按项目覆盖；规划模式不在项目创建页展示。状态派生为 `unavailable` 且不属于编辑态原有关联集合的 Agent 卡片需要追加 `disabled` class、`aria-disabled="true"` 和 tooltip，点击/回车/空格都不得触发选择 |
| `/settings` | 项目参数设置页 | 配置全局项目参数默认值（轮询间隔范围、启动延迟、默认 Task 超时时间）和全局 Prompt 设置（同机分配引导 textarea、恢复默认值）；仅管理员可访问；新项目创建时普通用户仍通过只读接口读取项目参数默认值作为初始表单值，并在创建时快照保存到项目 |
| `/projects/:id` | 项目详情页 | 核心工作台，"下一步"提示 + 状态总览 + 阶段入口 |
| `/projects/:id/plan` | 计划生成页 | 流程来源选择使用轻量分段控件，左侧为“使用模版生成流程”、右侧为“由 Prompt 生成流程”；默认优先模版路径，真实 `projectData + templateList` 返回后若无可用模版则自动切回 Prompt；用户手动选择会写入浏览器 localStorage，并在同一用户同一项目下恢复，非法值或不可用模版偏好回退默认。任务介绍说明文案应覆盖两种流程来源，表达其会保存到项目并用于后续规划或任务执行上下文，不应只绑定“生成 Prompt”。Prompt 路径：任务介绍输入与自动保存说明 + 规划模式选择 + 参与规划 Agent 勾选 + 每个 Agent 可选单一模型/留空自动选择 + Agent 卡片首行按“名称 + 空格 + agent type”展示 + Prompt 生成/复制分离 + 状态灯/计时器 + 后端按项目配置轮询检测，查到结果后自动定稿并跳转任务页。模版路径：任务介绍输入 + 流程模版选择 + 槽位到项目 Agent 的唯一映射，每个槽位下方展示角色说明，未填写时展示“暂无说明” + 按所选模版 `required_inputs` 渲染“模版所需信息”表单，`sensitive` 字段使用 password 输入框 + 任务介绍为空时就近提示“请先填写任务介绍。” + 必填模版输入为空时提示“请填写所有模版所需信息。”并禁用下一步 + 应用模版前保存 `{ goal, template_inputs }` + 应用模版后直接定稿并跳转任务页 |
| `/projects/:id/tasks` | 计划修改与执行页 | DAG 状态视图 + 节点底色区分状态 + 右侧文本自动保存 + Prompt 复制 + 异常处理操作；页面在进入/重新聚焦/手工刷新时同步最新任务状态 |
| `/projects/:id/summary` | 执行汇总页 | 任务状态总览 + 产出链接 + 人工介入记录 |
| `/templates` | 流程模版列表页 | 展示所有流程模版，包含名称、描述、角色数、槽位、创建/更新时间和可编辑操作；所有登录用户可查看和使用，创建者/管理员可编辑删除 |
| `/templates/new` | 新建流程模版页 | 页面上方先填写模版名称和适用场景，再输入详细流程描述生成模版编写 Prompt；适用场景作为 `scenario` 参与 Prompt 拼装，详细流程描述作为 `prompt_source_text` 随保存持久化；“输入描述”区块在“生成 Prompt”右侧提供“拷贝 Prompt”按钮，未生成 Prompt 时禁用，生成后复制当前 Prompt textarea 的 state（包含用户手工编辑），复制失败时通过页面顶部 `error-message` 展示 `拷贝 Prompt 失败：...`，且不得调用 `/dispatch`、`/poll` 或其他后端接口，不得触发路由切换、状态清空或计时器；粘贴/编辑 JSON 后预览 DAG，名称/适用场景仅在页面字段为空时由 JSON `plan_name` / `description` 回填，保存时页面字段优先、空则回退 JSON。页面进入新建态时清空上一次预览和角色说明状态；预览成功后按 `tasks[].assignee` 抽取 slot 并展示角色说明编辑区，JSON `agent_roles` 预填空说明，并可同步仍等于上一次 JSON 预填值的说明；页面提供必需输入信息编辑区，可添加、删除、排序并校验 `required_inputs`；必需输入信息编辑行必须使用稳定的前端本地 row id 作为 React key，不能使用用户正在编辑的 `required_inputs[].key`，保存 payload 时必须剥离该本地 row id；DAG 预览容器保持稳定高度，避免空白预览 |
| `/templates/:templateId` | 流程模版详情页 | 只读展示模版元数据、详细描述（`prompt_source_text`，为空显示“暂无说明”）、JSON、DAG 预览、每个 slot 的角色说明和已声明的必需输入信息 |
| `/templates/:templateId/edit` | 编辑流程模版页 | 创建者/管理员可修改名称、适用场景、详细流程描述、JSON、当前 slot 的角色说明和 `required_inputs`；页面展示完整的“1. 基本信息 / 2. 输入描述 / 3. 编辑 JSON”链路，进入时回填 `prompt_source_text` 和必需输入字段，并为每个必需输入编辑行补充稳定的前端本地 row id。“输入描述”区块同样提供“生成 Prompt”和“拷贝 Prompt”按钮，拷贝行为与新建页一致，仅写剪贴板，不触发派发、轮询、路由切换、状态清空或计时器。预览仅补齐空名称/适用场景；角色说明同步遵循“未手工编辑可随 JSON agent_roles 更新，已手工编辑不覆盖”；保存后重新抽取槽位并过滤已删除 slot 的说明，必需输入字段独立保存，不受 slot 抽取影响，且本地 row id 不进入 API 请求体 |
| `/agents` | 智能体总览页 | 单列卡片式布局（每 Agent 一行），用于管理可参与项目执行的Coding Agents；左侧展示名称+状态徽章、Agent 类型介绍（来自 agent_type_configs.description）、类型/多个模型徽章、逐模型能力描述（同一模型仅在自动排序首次出现时显示能力描述，后续去重），右侧展示短期/长期重置倒计时（不足阈值变色，自动续推后出现重置/确认按钮）。排序规则：默认自动排序（先按状态分组：可用→短期/长期重置后可用→不可用；可用组内按最近一次重置时间排序（取短期/长期中较早者，无重置时间的排末尾）；重置后可用组内按对应重置时间从近到远排列）；支持拖拽手动排序（持久化到 `display_order` 字段），手动排序后出现"自动排序"按钮可恢复默认。非"可用"状态（短期/长期重置后可用、不可用）的 Agent 卡片底色为淡灰色以视觉区分。新增/编辑使用模态表单，分四个区块卡片，支持动态增删多个模型配置；"设置"入口仅管理员可见 |
| `/agents/settings` | 智能体设置页 | Agent 类型与模型全局配置，仅管理员可访问。支持增删改 Agent 类型，每个类型下管理模型列表（名称、别名、能力描述≤150字）。输入时自动推荐匹配项。同名/同别名模型共享能力描述 |
| `/admin/users` | 用户管理页 | 仅管理员可访问。表格展示用户名、注册时间、最后登录时间、最后登录 IP、用户类型、用户状态；支持改角色、冻结、解冻。改角色和冻结前需确认；对自己和最后一个激活管理员禁用危险按钮 |

Agent 编辑接口说明：

1. `PATCH /api/agents/{id}/status` 仅更新 `availability_status`，不进入模型归一化逻辑。
2. `PUT /api/agents/{id}` 用于通用编辑，但实现上支持字段级局部更新；前端可以只提交发生变化的字段。
3. 当用户提交 `models` 数组时，后端重新序列化 `models_json` 并同步顶层主模型字段；未提交 `models` 时不触碰现有 `models_json`。

### 任务执行页布局（核心页面）

```
┌─────────────────────────────────────────────┐
│  项目名称                    [手动刷新按钮]    │
├─────────────────────────────────────────────┤
│  ⚡ 下一步提示区                              │
│  "当前应执行：将 Prompt 发送给 claude-code-01  │
│   执行 TASK-002（整理研究脉络）"               │
├──────────────────────┬──────────────────────┤
│                      │                      │
│   DAG 可视化区域      │   任务详情/操作面板    │
│   (React Flow)       │                      │
│                      │   - 任务名称          │
│   [TASK-001] ──→     │   - 状态灯           │
│       [TASK-002] ──→ │   - 分配 Agent        │
│           [TASK-003] │   - [复制 Prompt]     │
│                      │   - □ 输出剩余用量    │
│                      │   - 前序输出链接       │
│                      │   - 用量信息（如有）   │
│                      │   - [重新派发]        │
│                      │   - [标记完成]        │
│                      │   - [标记放弃]        │
└──────────────────────┴──────────────────────┘
```

### DAG 编辑交互

- DAG 视图用 React Flow 渲染节点与依赖边
- Plan 页面先展示流程来源选择，再按来源展示对应表单；不展示规划结果内容区。流程来源控件使用紧凑分段样式，顺序固定为左侧“使用模版生成流程”、右侧“由 Prompt 生成流程”，并在控件下方显示随当前来源变化的辅助说明。任务介绍不再需要单独点击确认按钮：Prompt 路径在生成 Prompt 前自动保存任务介绍和规划模式；模版路径在应用模版前自动保存任务介绍。任务介绍区的说明文案必须适用于两种流程来源，不能只描述“生成 Prompt”
- Plan 页面流程来源初始值为模版路径；真实 `projectData + templateList` 返回后按 `(projectData.agent_ids?.length ?? 0) >= template.agent_count` 判断是否存在可用模版。若没有可用模版，则自动切回 Prompt；用户手动选择任一来源后，后续加载、轮询刷新、页面聚焦刷新都不得覆盖用户选择。该判断不依赖完整 Agent 列表或 `projectAgents`
- Plan 页面流程来源偏好使用浏览器 localStorage，key 为 `plan_source_pref:{project.created_by}:{project_id}`，value 仅允许 `template` 或 `prompt`。无记录、非法 value、或 value 为 `template` 但当前项目没有可用模版时，统一回退到 `getInitialFlowSource(project.agent_ids, templates)`。同一 PlanPage 实例切换到另一个项目 id 时，必须重置自动选择门闩并重新读取新项目自己的偏好或默认值，避免项目间状态串扰
- Prompt 路径展示规划模式选择器；负责人点击“生成 Prompt”前，前端先保存任务介绍和当前 `planning_mode`
- Prompt 路径下每个已选 Agent 下方提供单选模型下拉框；若不选择，则前端提交空值，由后端基于项目目标文本、项目规划模式与模型能力描述做自动匹配；用户手动指定模型优先级高于模式建议
- 模版路径展示流程模版列表和槽位映射；同一项目 Agent 不能被映射给多个模版槽位，映射不完整时禁用“应用模版”。任务介绍为空或纯空白时页面在操作区显示“请先填写任务介绍。”。点击“下一步”时先校验任务介绍非空，再通过 `PUT /api/projects/:id` 保存 `{ goal: planningBrief }`，然后调用 `POST /api/process-templates/:templateId/apply/:projectId`；不得在该路径保存 `planning_mode`
- 负责人在 Tasks 页面选中“当前可执行”的任务后，可直接修改右侧任务名称、描述和预期输出
- MVP 不做拖拽连线编辑依赖，连线为只读展示
- 节点拖动布局：可支持，不强制保存位置

### 关键交互

- 点击 DAG 节点 → 右侧面板展示该任务详情和操作按钮
- "复制 Prompt"按钮 → 复制到剪贴板 + 任务状态自动转为"执行中" + 开始计时
- "输出剩余用量"勾选框 → 位于"复制 Prompt"按钮旁，勾选后 Prompt 附加用量输出指令
- Plan 页 Prompt 路径"生成 Prompt"按钮 → 先保存当前任务介绍和规划模式到项目，再生成 Prompt。若同项目已有可复用的未派发 candidate pending plan，则更新该记录并复用其 `plan_id/source_path`；否则创建一条新的 `project_plans.status=pending` 候选计划记录
- Plan 页模版路径"下一步"按钮 → 先保存当前任务介绍到项目 `goal`，再应用模版；若任务介绍为空或纯空白字符，前端阻止提交并提示“请先填写任务介绍。”。该路径不写入 `planning_mode`，也不把任务介绍写入模版 JSON 或 task.description
- Plan 页"生成 Prompt"时，后端会把用户显式选中的模型或自动匹配结果写入 `selected_agent_models_json`，并在 Prompt 的参与 Agent 列表中写明“本轮使用模型”
- Plan 页"生成 Prompt"时，后端会读取项目 `planning_mode`，在参与 Agent 说明之后、同机分配引导之前加入规划模式策略。`quality` 模式必须提示关键目标可拆成多个并行候选 task + 评审/合并 task，且不得让单 task 绑定多个 assignee；`cost_effective` / `speed` 模式在没有结构化成本/速度字段时通过模型能力描述进行软匹配
- Plan 页"生成 Prompt"时，后端会读取全局 `plan_co_location_guidance`，在参与 Agent 说明之后、输出要求之前加入同机分配引导；该引导始终加入，空白配置回退到默认文案
- Plan 页重复点击"生成 Prompt"时，在同一 pending 周期内不得改变 `source_path`；即使项目协作目录配置在两次生成之间变化，也必须优先保留数据库中已有的路径。复用时需要覆盖 `include_usage`、`selected_agent_ids_json`、`selected_agent_models_json`、`prompt_text` 和模型自动决策结果，并清空旧 `last_error`
- Plan 页"拷贝 Prompt"按钮 → 复制 Prompt + 将对应计划推进到 `running` + 启动或恢复后台轮询
- 若当前规划记录已处于 `running`，重复点击“拷贝 Prompt”不会创建新的轮询
- 若当前规划记录已 `completed` 或已超时结束，则再次点击“拷贝 Prompt”会新建一轮候选计划并重新进入 `running`
- Plan 页状态灯：黄灯 `pending/needs_attention`，红灯 `running`，绿灯 `completed/final`
- Plan 页计时器：仅在 `running` 且由当前页面会话触发的轮询中显示；从本次会话点击“拷贝 Prompt”时以 `00:00:00` 起始实时累加，不自动续接历史页面会话中的计时
- Plan 页轮询命中 `<collaboration_dir>/plan-<plan_id>.json` → 自动将 JSON 写入 `project_plans.plan_json`，并将计划状态更新为 `completed`
- 轮询读取顺序：优先检查 `/app/repos/<project_id>` 下的仓库副本；若未找到目标文件且配置了共享工作区挂载，则在校验共享工作区 `origin` 与项目 `git_repo_url` 一致后，再检查共享工作区中的同路径文件；若仍未命中，则直接读取远端跟踪分支（如 `origin/main`）上的同路径文件快照
- 若仓库副本执行 `git pull --ff-only` 失败（例如本地缓存分叉），轮询流程记录日志后继续使用当前 checkout、共享工作区回退源与远端跟踪分支快照完成本轮检测，不因 pull 失败直接中断
- Plan 页一旦检测到合法规划结果，前端立即调用定稿接口，将该轮规划转为最终计划，并自动跳转 `/projects/:id/tasks`
- Plan 页模版路径"应用模版/下一步"按钮 → 前端先保存当前任务介绍到项目 `goal`，再调用 `/api/process-templates/:templateId/apply/:projectId`；后端将抽象槽位替换为具体 Agent、创建最终任务并将项目推进到执行阶段，前端成功后直接跳转 `/projects/:id/tasks`
- 规划文件解析：优先按标准 JSON 解析；若失败，可对常见格式错误做一次自动修复后再解析，以提高实际 Agent 输出的兼容性
- "手动刷新"按钮 → 立即触发一次 Git 轮询，刷新所有任务状态
- Tasks 页节点底色：浅灰（不可执行/待执行）、浅黄（当前可执行）、浅橙（执行中）、浅红（执行异常）、浅绿（执行成功）、深灰（已放弃）
- 任务执行页中每个任务节点需展示指派 Agent 名称
- 项目创建/编辑页中的 Agent 状态徽章与 `/agents` 页面保持同一套四状态推导规则（可用 / 不可用 / 短期重置后可用 / 长期重置后可用）。不可用由 `subscription_expires_at` 实时推导，其余状态存储在 `availability_status` 中并支持通过 `PATCH /api/agents/:id/status` 切换
- 若任务存在前序依赖，则仅当前序任务全部为 `completed` 或 `abandoned` 时允许操作；否则“复制 Prompt”与“放弃任务”按钮都必须禁用；后端 `dispatch` 接口也必须拒绝该操作
- 当前可执行任务选中后，右侧文本字段改动 600ms 后自动调用 `PUT /api/tasks/:taskId` 保存；非当前可执行任务只读

---

## 六、实现优先级

建议按以下顺序推进：

1. SQLite 建表 + 后端 API 骨架
2. 前端页面骨架 + 路由
3. 项目创建 → 计划生成 Prompt → 计划导入与 DAG 渲染
4. 任务执行 Prompt 生成 → 复制 → Git 轮询 → 状态推进
5. 异常处理（超时、重新派发、手动标记）
6. Agent 总览页面
7. 执行汇总页面
8. Docker Compose 部署

---

## 七、前端加载性能约定

为缩短页面切换的首屏可见时间，前端遵循以下约定：

1. **路由级代码分割**：`src/frontend/src/App.tsx` 中除 `LoginPage`、`ProjectListPage` 外的页面统一通过 `React.lazy` + `<Suspense>` 加载，避免主包被任务页 / Agent 页 / `reactflow` 等重依赖拖大。
2. **跨页共享数据采用 stale-while-revalidate 缓存**：低频变化的接口（当前为 `/api/agents`、`/api/projects/:id`）通过 `api.getCached(path, onData)` 访问。命中缓存时立即用旧值渲染，再后台静默刷新；同 path 的并发请求会复用同一 in-flight Promise。若后台刷新失败但已有旧值，则回退保留旧值，不向页面抛出未处理 Promise 拒绝。
3. **写入后显式失效**：保存 / 删除 Agent、保存项目等写操作完成后，必须调用 `api.invalidate(path)` 让缓存丢弃旧值，避免 UI 看到过期数据。
4. **focus / visibilitychange 刷新做节流**：`TasksPage` 在 2 秒内最多触发一次刷新，避免与 5 秒轮询、StrictMode 双调用、快速切页叠加在一起。
5. **后端批量接口避免 N+1**：`GET /api/projects/:id/predecessor-status` 在单次请求内只查询一次 `Project`、一次 `Task` 列表，并对 `git_service.file_exists` 做请求级路径缓存，确保任务量增长时该接口的开销保持可控。

## 八、安全与硬化设计(2026-04-08)

本节固化当前公开版本中已经实现的安全与主流程修复设计。

### 8.1 启动期配置校验(`config.py::validate_security_config`)

- 检查 `HALF_SECRET_KEY`:不在内置弱密钥黑名单且长度 ≥32
- 检查 `HALF_ADMIN_PASSWORD`:满足统一密码强度规则（至少 8 位，包含大小写字母和数字），且不在弱口令黑名单
- 默认 `HALF_STRICT_SECURITY=true` 时,违规直接 `SystemExit(1)`；仅当显式设置 `HALF_STRICT_SECURITY=false` 时，才降级为 warning 并允许启动（不建议生产环境使用）
- 由 `main.lifespan` 启动期调用,先于 `init_db`

### 8.2 CORS 收紧

- `HALF_CORS_ORIGINS` 提供逗号分隔 allow-list，后端按该白名单配置 CORS，并启用 `allow_credentials=True`
- 默认值为本地开发 origin（`http://localhost:5173,http://localhost:3000`）；生产环境应显式覆盖
- `allow_methods` 显式列表(GET/POST/PUT/PATCH/DELETE/OPTIONS),`allow_headers` 仅 `Authorization`/`Content-Type`

### 8.3 Git URL 白名单(`services/git_service.validate_git_url`)

校验流程:
1. 空值 / 非字符串 → 拒绝
2. 前缀黑名单(`file://`/`ext::`/leading-dash) → 拒绝
3. 解析为 host:
   - `git@host:path` → 取 `@` 后到 `:` 前
   - 其他 → `urlparse`,scheme 仅允许 `https`/`ssh`
4. host 黑名单(`localhost`/`127.0.0.1`/`0.0.0.0`/`::1`/`169.254.169.254`)→ 拒绝
5. 私网前缀(`10.`/`192.168.`/`172.16~31.`)→ 拒绝

由 `routers/projects.py` 在 create 与 update 入口调用。

### 8.4 文件路径安全与单一真相源(`services/path_service.py`, `services/git_service.py`)

```
candidate = realpath(base) + relative_path
if candidate not under realpath(base): raise PermissionError
```

`git_service._safe_join` 被 `read_file`、`file_exists`、`list_dir`、`dir_has_content` 统一使用。`expected_output` 的唯一归一化入口为 `services.path_service.resolve_expected_output_path` / `normalize_expected_output_path`：

- `routers/plans.finalize_plan`、`routers/tasks.update_task` 以 `strict=True` 调用，拒绝动作短语、绝对路径、`..` 越界和无法唯一解析的脏值
- `expected_output_path` 继续保留为展示字段，但任务 Prompt、轮询检测、usage 路径和前序任务目录展示统一采用固定任务目录契约：`{collaboration_dir}/{task_code}/`
- `task.result_file_path` 命中后始终回写为固定路径 `{collaboration_dir}/{task_code}/result.json`

### 8.5 Plan finalize 字段兼容(`routers/plans._normalize_task_fields`)

进入任务创建循环之前,先把 task dict 中的:
- `predecessors → depends_on`
- `title → task_name`
- `agent_id → assignee`(int 转 str 后由 `_resolve_assignee_agent_id` 解析)

Plan import 同时接受 `str | dict` 两种 `plan_json`。

### 8.6 redispatch 状态白名单

`POST /api/tasks/{id}/redispatch` 接受的源状态:`needs_attention` / `running` / `abandoned`。事件 detail 记录 `prev_status` 以便审计。

### 8.7 轮询错误传播与 Git 同步策略(`services/polling_service.py`, `services/git_service.py`)

`git_service.ensure_repo_sync(project_id, git_repo_url)` 是轮询前唯一允许调用的同步入口：

- 同一项目在 TTL 窗口内复用最近一次同步结果，避免同一 polling interval 内重复 `git fetch`
- `git fetch origin` 遇到网络抖动时按有限次重试 + 指数退避执行；`git pull --ff-only` 失败不会被吞掉，而是以 warning 返回给 `poll_project`
- fetch 成功后，轮询读取优先使用 `origin/HEAD` 快照（`prefer_remote=True`），避免本地脏工作树、非默认分支或 fast-forward 失败导致读到旧文件
- 检测过程中若发生 git fetch/clone 等远端不可达的同步失败，任务/计划保留 `running` 状态但写入 `last_error`，并跳过本轮 timeout 判定；同步异常不得伪装成 “result not found”
- 若 `git fetch origin` 成功但 `git pull --ff-only` 因本地缓存分叉失败，`poll_project` 只记录 `logger.warning`，不写入 `last_error`，不创建 `error` 类型 `TaskEvent`，并继续执行计划/任务结果检测；如果结果仍未出现且已超过超时时间，应正常进入 timeout / `needs_attention`
- 任务结果检测固定为 `{collaboration_dir}/{task_code}/result.json`；`usage.json` 固定为同目录 `{collaboration_dir}/{task_code}/usage.json`
- `needs_attention` 状态任务继续参与轮询；若后续检测到 `result.json`，任务自动恢复为 `completed` 并清除 `last_error`
- 已处于 `needs_attention` 且仍未检测到结果的任务，不重复写入 timeout 事件，也不重复刷新同类 timeout `last_error`
- `git_service.dir_has_content(project_id, rel, prefer_remote=True)`:优先递归检查远端目录是否存在文件

### 8.8 Task 超时时间配置

- 全局默认 Task 超时时间存储在 `global_settings.task_timeout_minutes`，默认 10 分钟，允许范围 1-120 分钟。
- 新建项目时，后端将当前全局默认值写入 `projects.task_timeout_minutes`；项目级值后续可在项目创建/编辑页修改，既有 Task 不受项目级修改追溯影响。
- 项目更新接口若显式提交 `task_timeout_minutes: null`，后端写入当前全局默认值，避免项目级字段长期保持空值。
- 最终计划生成 Task 时，将 `projects.task_timeout_minutes` 写入 `tasks.timeout_minutes`，使每个 Task 在创建后拥有自己的超时时间快照。
- Task 详情页仅在 `pending` 状态允许编辑 `timeout_minutes`；`running`、`needs_attention`、`completed`、`abandoned` 状态只读展示。
- 轮询超时判定统一调用 `polling_service.get_effective_task_timeout_minutes`，兜底顺序为 Task 值、项目级默认、全局默认、10 分钟。
- UI 只展示字段名“超时时间”，不展示继承来源。

### 8.9 规划 Prompt 同机分配引导

- 默认文案由 `services.prompt_settings.DEFAULT_PLAN_CO_LOCATION_GUIDANCE` 维护，设置 key 为 `plan_co_location_guidance`。
- `services.prompt_settings.get_plan_co_location_guidance(db)` 负责读取全局设置；数据库无记录或值为空白时回退默认文案。
- `services.prompt_settings.upsert_plan_co_location_guidance(db, value)` 负责写入；非字符串、空字符串和纯空白值必须拒绝。
- `routers.settings` 提供 `/api/settings/prompt` GET/PUT；GET 要求登录用户，PUT 要求管理员。
- `routers.plans` 在首次生成规划 Prompt 和 completed/final 计划重新生成副本时读取最新同机分配引导，并传入 `generate_plan_prompt()`。
- `services.prompt_service.generate_plan_prompt()` 保持不直接依赖数据库，通过可选 `co_location_guidance` 参数接收文案，并在参与 Agent 说明之后、输出要求之前拼接。该逻辑不改变 dispatch、轮询、Git 拉取、Task 超时或服务器操作权限。

### 8.10 规划模式（planning_mode）

- `models.Project.planning_mode` 存储项目级规划策略，默认 `balanced`；`main.ensure_schema_updates()` 负责给既有 SQLite 表补列并将空值回填为 `balanced`。
- `routers.projects` 维护规划模式白名单：`balanced` / `quality` / `cost_effective` / `speed`。创建/更新项目时非法值返回 400；响应统一通过 `_build_project_response()` 返回归一化后的 `planning_mode`。
- 前端 `utils/planningMode.ts` 维护四种模式的 value、label、description 和默认归一化逻辑；`ProjectNewPage` 不再展示规划模式；`PlanPage` 在 Prompt 路径下展示模式选择器，并在生成 Prompt 前通过项目更新接口保存 `planning_mode`。
- `services.prompt_service.generate_plan_prompt()` 读取项目 `planning_mode` 并注入 `PLAN_MODE_GUIDANCE`。策略段落位于参与 Agent 说明之后、同机分配引导之前、输出要求之前。
- 自动模型选择仍以用户手动指定模型为最高优先级；未指定时，`resolve_selected_agent_models()` 会把规划模式转换为轻量关键词 hint 参与能力描述匹配。该逻辑是软匹配，后续若需要强确定性，应在模型配置中增加结构化 `cost_tier` / `speed_tier` / `quality_tier` 字段。

### 8.11 流程模版（process_templates）

- `models.ProcessTemplate` 存储可复用流程模版；`main.py` 注册 `/api/process-templates` 路由。
- `routers.process_templates.validate_template_json()` 是模版 JSON 的统一校验入口，负责必填字段、槽位格式、依赖引用、DAG 无环、`expected_output` 路径归一化和槽位抽取。
- 模版名称最终不能为空。创建/更新时，请求 `name` 为空或未传则从 JSON `plan_name` 派生；二者都为空时返回 400。描述允许为空，请求 `description` 为空或未传时从 JSON `description` 派生。
- `prompt_source_text` 存储用户在“输入描述”中填写的详细流程描述。创建时直接写入请求值；更新时未传表示保留原值，传空字符串表示清空，传非空字符串表示覆盖。列表和详情响应均返回该字段，`NULL` 序列化为 `null`。
- `required_inputs_json` 存储模版必需输入声明。创建时未传则保存 `[]`；更新时未传表示保留原值，传入数组时按统一规则校验并覆盖。每项仅支持最小字段集：`key` / `label` / `required` / `sensitive`。后端必须拒绝非数组、非对象项、非法 key、重复 key、空 label，以及非 boolean 的 `required/sensitive`。
- `POST /api/process-templates/generate-prompt` 请求体包含 `scenario` 和 `description`：`scenario` 对应页面“适用场景”，允许为空；`description` 对应页面“流程描述”，不能为空。生成的 Prompt 中 `scenario` 作为“适用场景 / 流程目标上下文”独立段落，`description` 作为“详细流程需求”独立段落。
- `agent_roles_description_json` 存储每个当前 slot 的角色说明。后端写入前统一 trim、过滤空值和非当前 slot；读取时解析失败返回空对象，避免坏历史数据影响列表/详情接口。
- 创建/编辑接口接受 `agent_roles_description`。创建时可为空；更新时未传该字段表示保留仍存在 slot 的旧说明，传空对象表示清空全部说明。说明数据只参与 UI 辅助展示，不参与模版应用的权限、数量、映射或任务生成逻辑。
- 所有登录用户可列出、查看和应用流程模版；只有创建者和管理员可更新或删除，响应中返回 `can_edit` 供前端控制操作入口。
- 应用模版时只允许项目处于 `draft` 或 `planning`。请求的 `agent_mapping` key 必须与模版槽位完全一致，不能缺失、额外或重复映射；映射值必须是当前用户拥有且已绑定到该项目的 Agent。
- 应用模版的后端接口保持契约稳定，请求体只包含 `slot_agent_ids`。Plan 页面在调用 apply 前先走项目更新接口保存 `{ goal, template_inputs }`；任务介绍和模版必需输入不进入 apply 请求体，不污染模版 JSON 或生成任务的 `description`。
- 应用成功后，后端先删除同项目旧的未选中候选计划，再将模版中的 `agent-N` 替换为对应 Agent slug，创建 `source_path = template:<template_id>` 的 completed candidate plan，并复用 `finalize_plan_record()` 生成 final plan 和 tasks。
- `polling_service.poll_project()` 遇到 running plan 的 `source_path` 以 `template:` 开头时只记录 warning 并跳过 Git 读取，避免把模版来源误判为仓库路径。
- 前端新增 `ProcessTemplatesPage`，并在 `App.tsx` 注册 `/templates`、`/templates/new`、`/templates/:templateId`、`/templates/:templateId/edit`。新建/编辑页顶部展示“模版名称”和“适用场景”，随后展示“输入描述”和 Prompt 结果；编辑态同样回填并保存 `prompt_source_text`，第三段标题为“3. 编辑 JSON”。“输入描述”区块在“生成 Prompt”右侧提供“拷贝 Prompt”按钮；该按钮未生成 Prompt 时禁用，生成后通过 `copyText(generatedPrompt, navigator.clipboard)` 复制当前 state，失败时设置页面错误条，不能触发 dispatch、poll、路由跳转、状态清空或计时器。预览 JSON 时仅在页面字段为空时从 JSON 回填名称/适用场景，不覆盖用户输入。`utils/processTemplateRoles.ts` 负责从任务 assignee 抽取 slot、从 JSON `agent_roles` 解析预填说明、按最新 slot 同步说明状态和构造保存 payload；预览同步必须能更新仍等于上一次 JSON 预填值的说明，同时保留用户手工编辑过的 slot。角色说明输入区仅在预览抽取到 slot 后展示，不能独立增删 slot；必需输入信息编辑区独立于 slot，可添加、删除、上移、下移字段，并在 key 非法、key 重复或 label 为空时禁用保存。每个必需输入编辑行使用前端本地 `rowId` 维持 React key 稳定，新增行和服务端回填行都要生成 rowId；`required_inputs[].key` 是业务字段且可编辑，不能参与 React key；保存请求只提交 `key`、`label`、`required`、`sensitive`。新建页切入时清空旧预览、说明状态和必需输入状态。DAG 预览区使用稳定高度容器并在任务集合变化时重建预览实例，避免空白预览。`PlanPage` 新增流程来源选择，Prompt 路径保留原计划生成链路，模版路径隐藏 Prompt 表单并展示槽位映射、每个槽位的角色说明，以及所选模版 `required_inputs` 对应的“模版所需信息”表单。流程来源默认优先模版路径，并通过真实数据校正处理无可用模版项目；用户手动选择后按 `project.created_by + project_id` 写入 localStorage，后续进入同项目时恢复合法偏好。

### 8.12 执行 Prompt 的项目任务介绍

- `services.prompt_service.generate_task_prompt()` 全局读取任务所属项目的 `goal`。当 `(project.goal or "").strip()` 非空时，在身份句 `你是项目 [X] 的执行 Agent。` 之后、`## 执行前置步骤` 之前插入 `## 项目任务介绍` 段，段落内容为 strip 后的 goal 原文。
- 当 `project.goal` 为 `None`、空字符串或纯空白字符时，`## 项目任务介绍` 整段完全省略，不输出标题、不输出“未提供”等占位文案，并保持身份句与执行前置步骤之间只有一个空段分隔。
- 该逻辑对所有 task 全局生效，不按流程来源区分。Prompt 路径、模版路径以及数据库中已存在的 task 只要所属 project 的 `goal` 非空，下一次生成执行 Prompt 时都会带上该段。
- 对来源于流程模版的 task，`generate_task_prompt()` 通过 `task.plan_id -> ProjectPlan.source_path` 解析 `template:<template_id>`，再读取对应 `ProcessTemplate.required_inputs_json` 和项目 `template_inputs_json`。若存在声明字段对应的非空值，则在 `## 项目任务介绍` 之后、`## 执行前置步骤` 之前插入 `## 模版所需信息` 段，按模版声明顺序渲染 `- {label}: {value}`。未声明的多余 key 必须忽略；无法追溯模版、模版不存在、JSON 非法或所有值为空时整段省略。
- `Project` 不新增 `process_template_id`。模版来源只通过计划记录的 `source_path = template:<template_id>` 追溯，避免在项目级别保存易过期的模版指针。

### 8.13 Plan 页稳定性修正

- `routers.plans.plan_generate_prompt()` 在创建新候选计划前先查找可复用的 pending plan。可复用条件为：同项目、`plan_type="candidate"`、`status="pending"`、`dispatched_at IS NULL`、`detected_at IS NULL`、`plan_json IS NULL`、`is_selected = false`。若存在多条符合条件的记录，复用 `id` 最大的一条。
- 复用 pending plan 时，`source_path` 必须优先使用已有 `plan.source_path`；仅旧值为空时才按 `_plan_file_path(project, plan.id)` 回填。这样同一 pending 周期内重复生成 Prompt、调整 Agent/模型或项目协作目录变化，都不会改变外部 Agent 已收到的 `plan-<id>.json` 输出路径。
- 复用 pending plan 时，后端更新 `include_usage`、`selected_agent_ids_json`、`selected_agent_models_json`、`prompt_text` 和自动解析后的模型选择结果，并清空 `last_error`。点击“拷贝 Prompt”后 plan 进入 `running` 并设置 `dispatched_at`，后续再次“生成 Prompt”会新建下一轮候选计划。
- `utils/flowSource.ts` 维护 Plan 流程来源偏好工具：`buildPlanSourcePrefKey()`、`isFlowSource()`、`resolveFlowSourcePreference()`。偏好只存浏览器 localStorage，不进入后端 DB，也不需要迁移。
- `PlanPage` 在用户点击流程来源分段控件时立即写入 localStorage；页面数据就绪后读取偏好。读取时必须先计算 `getInitialFlowSource(project.agent_ids, templates)`，并仅在存储值合法且不会落到不可用模版路径时覆盖默认值。
- `PlanPage` 使用项目 id ref 跟踪当前页面实例所属项目。若同一 SPA 实例从项目 A 直切项目 B，必须重置流程来源自动选择门闩，重新读取项目 B 的偏好或默认值；同一项目内的轮询刷新、focus 刷新和数据重拉不得覆盖用户已手动选择的来源。

### 8.14 留待 follow-up(本轮未实现)

- 更细粒度的多角色权限模型(RBAC/协作者共享项目):当前已实现管理员 / 普通用户区分与 owner 级业务隔离，但尚未支持协作者共享项目、项目级成员管理和更细粒度的 RBAC
- 登录限速与失败锁定(F-P1-02):中间件已落位骨架,待接入路由
- JWT 黑名单/refresh、日志脱敏、DB 索引、CI 门禁等 P2/P3 项
