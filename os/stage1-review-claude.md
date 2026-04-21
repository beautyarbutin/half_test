# 开源准备阶段一 — Code Review（Claude）

- **仓库：** `keting/half`
- **审查分支：** `audit/stage1-open-source`
- **基线：** `origin/main`
- **提交：** `3f02221 security: stage 1 hardening for open source prep`

## Findings

### Med-1 — 管理员密码校验没有落实文档里写的规则
- **Severity:** Medium
- **File:** `src/backend/config.py:66`
- **Problem:** `validate_security_config()` 只检查 `len(settings.ADMIN_PASSWORD) >= 8` 以及是否在 `_DEFAULT_INSECURE_PASSWORDS` 名单里，并没有强制"必须包含大写、小写和数字"。但 `.env.example:10-14` 和 `README.md:39-40` 都明确承诺了这条规则。也就是说 `HALF_ADMIN_PASSWORD=aaaaaaaa` 或 `bbbbbbbb1` 都能通过 strict 校验，违反文档，也违反 register / change-password 通过 `_PASSWORD_PATTERN` 强制的同一条规则。
- **Why it matters:** 阶段一第 5 条目标就是"统一密码规则到 8 位 + 大小写 + 数字"。系统里最敏感的凭证——初始管理员密码——反而比任何后续用户密码更宽松。同文件内已有 `validate_user_password()`，`validate_security_config()` 应该直接复用它来校验 admin。

### Med-2 — 后端直接运行（不走 docker-compose）时弱默认值依然生效
- **Severity:** Medium（与阶段一目标偏离）
- **File:** `src/backend/config.py:27-28`, `src/backend/config.py:38`
- **Problem:** Python 里 `SECRET_KEY` 仍然默认为 `"example-insecure-secret-placeholder"`，`ADMIN_PASSWORD` 仍然默认为 `"example-insecure-password-placeholder"`，`HALF_STRICT_SECURITY` 默认为 `"false"`。fail-fast 只写在 `docker-compose.yml` 里。任何直接 `uvicorn main:app` 的路径（开发机、二次打包、Kubernetes、systemd）都会拿到这些硬编码默认值，而且只打 warning 日志就继续启动。
- **Why it matters:** 提交信息写着 "stage 1 hardening"，但 hardening 完全依赖 compose 这层外壳，应用自身没守住底线。最低限度应当把 `HALF_STRICT_SECURITY` 的 Python 默认改成 `true`；更稳妥的做法是把 `SECRET_KEY` / `ADMIN_PASSWORD` 的默认值改成 `None` 并在启动时直接崩溃。否则对所有非 compose 启动路径而言，这个地雷还在。

### Low-1 — CORS 通配符 fallback 已是死代码，注释误导
- **Severity:** Low
- **File:** `src/backend/main.py:409-419`
- **Problem:** `config.py:40` 把 `HALF_CORS_ORIGINS` 的默认值改成了 `"http://localhost:5173,http://localhost:3000"`，所以 `_cors_origins_raw` 在正常运行下永远非空，`main.py:414-419` 里 `else` 分支（退回到 `["*"]` + `allow_credentials=False`）实际上不可达。注释却仍然写着 *"Operators should set HALF_CORS_ORIGINS in production"*，暗示不设置就会变成通配符，这已经不成立。
- **Why it matters:** 要么把这段 fallback 删掉（默认值本身已经安全），要么把默认值改回 `""` 让 fallback 真正生效。维持现状，读者会被注释误导，并且存在死代码；在 dev 环境习惯了"不设 `HALF_CORS_ORIGINS` → 通配符"的运维会被默默改变行为。

### Low-2 — 前端 `validatePassword` 有冗余判断
- **Severity:** Low
- **File:** `src/frontend/src/pages/LoginPage.tsx:9-14`
- **Problem:** 前 4 条检查（`length < 8`、缺小写、缺大写、缺数字）已经覆盖了 `PASSWORD_REGEX` 会拒绝的所有情况，第 13 行 `if (!PASSWORD_REGEX.test(pw)) return '...';` 永远不会命中。
- **Why it matters:** 功能上无害，但模糊了真正的校验来源。将来只修一处规则时就可能让 regex 与 4 条独立检查之间悄悄走样。应当只保留一个 source of truth（要么 regex，要么四条检查）。

### Low-3 — 默认 compose 里还在声明 `HALF_WORKSPACE_ROOT`，但不再挂载 `/workspace`
- **Severity:** Low
- **File:** `src/docker-compose.yml:13`
- **Problem:** `HALF_WORKSPACE_ROOT=/workspace` 仍然被无条件注入，但 `..:/workspace` 这条 bind mount 已经被删掉。除非操作者复制 override 模板并启用 workspace 挂载，否则容器里根本没有 `/workspace`。依赖它的 `services/git_service.py:311,342` 依靠 `os.path.isdir` 静默降级。
- **Why it matters:** 这是"恰好不出错"。建议要么把这个环境变量从默认 compose 里删掉、放进 `docker-compose.override.yml.example`（与它配套的 mount 在同一处），要么在默认 compose 里不设 `HALF_WORKSPACE_ROOT`，让 override 同时提供 mount 和环境变量。现在这个变量只有在启用 override 时才有意义，单独留在默认 compose 里是遗留物。

### Low-4 — `.env.example` 声称 `HALF_STRICT_SECURITY` 默认 `true`，后端不认
- **Severity:** Low
- **File:** `src/.env.example:18-19`
- **Problem:** 注释写的是 *"Refuse to start if HALF_SECRET_KEY / HALF_ADMIN_PASSWORD look weak. Default: true."* 但后端代码 `config.py:39` 的实际默认是 `false`，只有 `docker-compose.yml:10` 把它提升到 `true`。对于不走 compose 的运行方式，注释里宣传的"默认值"是错的。
- **Why it matters:** 次要，但和 Med-2 是同一个"文档 / compose / 代码"三方不一致问题。把 Python 默认改成 `true` 可以让三处说法统一。

### Info — 阶段一目标 1 / 2 / 3 / 4 / 6 / 7 / 8：已达成
- `example-org/example-repo` / `git@github.com:example-org/example-repo.git` 在 `src/**` 已清零；剩下的三处引用都在预期范围内（`_DEFAULT_INSECURE_PASSWORDS`、`.env.example`、`README.md` 的 "never use" 示例）。
- compose 弱默认值已收紧；在未设置 `HALF_ADMIN_PASSWORD` / `HALF_SECRET_KEY` 时 `docker compose config` 会直接报插值错误退出。
- `..:/workspace` 与 `${HOME}/.ssh:/ssh-host:ro` 挂载、以及容器内 `rm -rf /root/.ssh` 的 shell 命令已全部移除。
- `docker-compose.override.yml.example` 存在，并被 `.gitignore` 正确忽略。
- `.env.example` 存在，`.env` 和 `docker-compose.override.yml` 也都在 `.gitignore` 中。
- README Quick Start 已重写，没有再出现弱默认值，并正确引导到 `.env.example` 和 override 模板。
- CORS 变量只剩 `HALF_CORS_ORIGINS`，`HALF_CORS_ORIGINS` 包括注释已全部删除。
- 改动范围克制，没有夹带阶段二的内容。

## 残余风险 / 测试缺口
- **无法在本地跑完测试套件。** 本机 Python 是 3.6（项目要求 3.12），前端 `node_modules` 未安装，所以 `pytest tests/test_git_service.py tests/test_polling_service.py tests/test_task_predecessor_status.py`、`npm test`、`npm run build` 都没能实际执行。`test_rq0410_1.py` / `test_admin_user_management.py` 里的密码 fixture（`Admin123`、`Alice123` 等）都是 8 位且满足 U+L+digit，对新的 `{8,}` 规则静态上兼容，没有红旗；但合入前仍需在 3.12 环境里跑一遍。
- **阶段一自身没有补充测试覆盖。** 没有针对 `validate_security_config()` 的用例（强密码通过、弱密码在 strict 下直接退出、非 strict 下只 warning）；没有针对 `validate_user_password()` 的用例；也没有针对"注册 7 位密码应被拒绝、错误文案已更新为 8 位"的回归用例。`LoginPage.tsx` 与 `Layout.tsx` 里的 regex 字面量是靠注释维持与后端一致，同一条规则目前有三份手写副本，存在漂移风险。
- **没有在真实部署里做运行时验证。** `docker compose config` 只证明了 compose 插值阶段 fail-fast 正常，没验证容器启动后的行为：`HALF_STRICT_SECURITY=true` 下弱 `HALF_SECRET_KEY` 是否真的拒绝启动、强密码下 admin 能否正常 bootstrap、`allow_credentials=True` 路径下 CORS allowlist 是否被正确应用、未挂载 SSH 时私有仓 clone 是否有合理报错。
- **`HALF_ALLOW_REGISTER` 默认值翻转会让老部署感知到回归。** 从 `true` 变成 `false`——这是有意的且文档上有说明，但是任何依赖"开放注册"的自托管站点升级后会直接无法注册新用户，需要在 release notes 或升级指引里显式提醒。
