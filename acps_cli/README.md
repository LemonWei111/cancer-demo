# acps-cli

`acps-cli` 是 ACPs 的统一命令行工具集，提供 Registry、CA、Discovery、MQ 四类客户端能力，面向开发联调、调试验证、standalone/provision 引导和日常运维脚本使用。

## 1. 概述

### 1.1. 项目定位

本项目是纯 CLI 工具，不启动 FastAPI、数据库或消息队列服务；无论是本地开发还是通用打包部署，都需要通过外部后端服务完成联调。

主要能力包括：

- Registry 用户端：登录、注册或更新 Agent、提交审核、获取 EAB、同步 ACS
- Registry 管理端：审核、启用、禁用 Agent
- CA 客户端：申请、续期、吊销证书，轮转 ACME 账户密钥，检查证书状态
- Discovery 客户端：触发 DSP 同步、执行查询、检查服务健康状态
- MQ 客户端：检查 mq-auth-server 健康状态，管理 Group ACL，并探测 Auth API allow / deny 决策

### 1.2. 命令与文档

当前统一入口是 `acps-cli`，主要命令域如下：

- `acps-cli auth` / `agent` / `entity`：Registry 用户侧操作
- `acps-cli cert`：证书生命周期与 EAB 相关操作
- `acps-cli discover`：Discovery 查询与状态查看
- `acps-cli admin registry ...`：Registry 管理面命令
- `acps-cli admin ca ...`：CA 管理面命令
- `acps-cli admin discovery ...`：Discovery 管理面命令
- `acps-cli admin mq ...`：mq-auth-server 管理面命令

所有 CLI 均支持：

- `--config PATH`：显式指定 `acps-cli.toml`
- `--verbose`：输出 DEBUG 日志，默认输出 INFO 及以上日志

其中需要按服务域临时覆盖地址时，可在对应命令组上使用 `--server-url`；Registry 相关命令额外支持 `--timeout`。

## 2. 开发

### 2.1. 开发环境与前置条件

本项目通常与以下兄弟仓库一起组建开发环境：

```text
acps/
  acps-infra/
  registry-server/
  ca-server/
  discovery-server/
  mq-auth-server/
  acps-cli/
```

- `uv`（[安装文档](https://docs.astral.sh/uv/getting-started/installation/)）—— `uv` 会根据 `.python-version` 自动下载并管理 Python 3.14，无需手动安装 Python
- `just`（[官方安装文档](https://just.systems/man/en/packages.html)）
- Docker Desktop（仅用于启动 `acps-infra/dev-infra` 依赖）
- 同级目录已存在 `../acps-infra/` 与四个后端兄弟仓库

补充说明：本仓开发统一使用 Python `3.14`，并通过仓库根目录 `.python-version` 固定版本请求；`just dev bootstrap` 会通过 `uv` 强制使用 managed Python `3.14` 创建与同步 `.venv`。

### 2.2. 建立 CLI 开发环境

`acps-cli` 是纯 CLI 工具，不提供 `just app` domain；本仓开发主路径是 `just dev bootstrap`，只负责准备 CLI 自身运行环境和 shared `dev-infra` 依赖。

```bash
just dev bootstrap
```

`just dev bootstrap` 会执行 `infra up postgres/redis/rabbitmq + prep env + prep sync + prep hooks` 等操作。

开发配置约定：

- 仓库根目录已提供 `acps-cli.toml` 作为本地开发默认配置；请根据实际情况调整其中的服务地址，确保它们能联通后端服务实例。
- 用户名/密码建议通过命令行选项提供；若未提供，`auth login` / `admin auth login` 会交互式提示输入
- 如需给 auto-register 提供默认显示名称和组织名，可在 `acps-cli.toml` 的 `[registry]` 中设置 `display_name` / `org_name`
- `just prep env` 仍可用于生成 `.env` 占位文件，供未来的环境变量覆盖场景使用
- 具体命令树可通过 `acps-cli --help`、`acps-cli cert --help`、`acps-cli admin --help` 查看

如果当前只是在修改 CLI 本体、查看帮助或调试单个命令，不需要先启动四个兄弟服务；只有在需要真实联调时，才进入下一节。

### 2.3. 启动本地联调环境

当你需要手工联调 Registry、CA、Discovery、MQ 四个后端服务时，先在 `acps-cli` 仓库执行：

```bash
just dev bootstrap
```

然后在**独立终端**中分别启动四个后端服务。请自行配置这些应用服务，确保它们能联通对方，以下为命令示例：

```bash
# 终端 1
cd ../registry-server && APP_ENV=development CA_SERVER_MOCK=false just app bootstrap && APP_ENV=development CA_SERVER_MOCK=false just app

# 终端 2
cd ../ca-server && APP_ENV=development REGISTRY_SERVER_MOCK=false just app bootstrap && APP_ENV=development REGISTRY_SERVER_MOCK=false just app

# 终端 3
cd ../discovery-server && just app bootstrap && just app

# 终端 4
cd ../mq-auth-server && just app bootstrap && just app
```

联调时建议注意以下几点：

- `registry-server` 联调时建议使用 `APP_ENV=development CA_SERVER_MOCK=false`，以启用真实 CA 吊销通知链路。
- `registry-server` 与 `ca-server` 需要使用同一个 `REGISTRY_SERVER_INTERNAL_API_TOKEN`，上面的示例统一使用 `local-registry-server-internal-api-token`。
- `mq-auth-server` 的 `9007` / `9008` 都要求 mTLS；本地联调时请确保 `../mq-auth-server/certs/` 或你自己的 `[mq]` 客户端证书配置已经就绪，`just doctor` 会把 MQ 与其它三个服务一起检查。

四个服务启动完成后，回到 `acps-cli` 仓库执行：

```bash
just doctor
```

如果 `doctor` 失败，它会明确告诉你缺的是哪个 HTTP 服务，并打印对应仓库的启动命令。

本地常用地址：

| 服务                     | 地址                     | 说明                                 |
| ------------------------ | ------------------------ | ------------------------------------ |
| registry-server          | `http://localhost:9001`  | `acps-cli.toml` `[registry]` 直连    |
| ca-server                | `http://localhost:9003`  | `acps-cli.toml` `[ca]` 直连          |
| discovery-server         | `http://localhost:9005`  | `acps-cli.toml` `[discovery]` 直连   |
| mq-auth-server Group API | `https://localhost:9007` | `acps-cli.toml` `[mq].group_api_url` |
| mq-auth-server Auth API  | `https://localhost:9008` | `acps-cli.toml` `[mq].auth_api_url`  |

联调命令示例：

```bash
uv run acps-cli auth login --username alice --password 'S3cret!'
uv run acps-cli agent save --acs-file acs.json
uv run acps-cli cert status --aic <AIC>
uv run acps-cli discover query "北京旅游推荐"
```

补充说明：

- `discover query` 默认直接输出 JSON，不需要再追加 `--json`。
- 如果要做稳定的 discovery 可见性 gate，建议改用 `discover query --type filtered --filter-json ...`。

例如：

```bash
uv run acps-cli discover query \
  --type filtered \
  --filter-json '{"conditions":[{"field":"aic","op":"eq","value":"<partner-aic>"},{"field":"active","op":"eq","value":true}]}'
```

### 2.4. 日常开发命令

常用开发命令：

```bash
just dev bootstrap
just doctor
just package wheel
just package wheel offline
just qa
just qa type
```

## 3. 测试

`acps-cli` 是四个 server 仓库之外，唯一承载真实跨服务联调 e2e 的仓库。

### 3.1. 测试职责与分层

测试边界约定如下：

- `registry-server`、`ca-server`、`discovery-server`、`mq-auth-server` 各自负责本服务的 `unit`、`integration` 和 self-contained `e2e`。
- 只要测试需要同时验证多个兄弟服务的真实交互，就应该归到 `acps-cli/tests/e2e/`，而不是继续留在 server 仓库。
- 典型联调场景包括：ATR / EAB / 证书申请主链路、证书生命周期状态传播、discovery snapshot / incremental / webhook / runtime 协作，以及 mq group / auth-probe 工作流。
- 少数明确标注为未来工作的场景允许保留 `skip`；除此之外，联调测试的目标是通过自动准备前置条件实现尽可能全绿。

| 层级       | 命令                    | 说明                                                                                    |
| ---------- | ----------------------- | --------------------------------------------------------------------------------------- |
| 单元测试   | `just test unit`        | 纯 mock，无外部服务依赖                                                                 |
| 集成测试   | `just test integration` | 以 CLI 自身参数、配置、输出和单服务命令契约验证为主；默认本地地址缺服务时由夹具自动托管 |
| 端到端测试 | `just test e2e`         | 真实跨服务联调主入口；默认本地地址缺服务时由夹具自动托管                                |
| 全量测试   | `just test`             | 运行全部测试                                                                            |

职责划分建议：

- `just test integration`：侧重 CLI 命令面、配置解析、输出格式、单服务命令契约。
- `just test e2e`：侧重跨服务用户旅程、真实状态传播、联调拓扑协作。
- `just test`：顺序执行 CLI 的 unit / integration / e2e；默认本地地址缺服务时由测试夹具补齐。

与四个 server 仓库的对应关系：

- `registry-server`、`ca-server`、`discovery-server`、`mq-auth-server` 各自的 `integration` 与 `e2e` 负责本服务自闭环验证。
- `acps-cli/tests/e2e/` 负责把四个服务串起来做真实联调回归。
- 如果某个场景必须同时启动多个兄弟服务，它应优先进入 `acps-cli/tests/e2e/`，而不是回流到 server 仓库。

### 3.2. 建立测试环境

测试入口统一使用：

```bash
just test bootstrap
```

`just test bootstrap` 与 `just dev bootstrap` 复用同一段共享准备逻辑，但语义上专门面向测试环境准备。

测试环境有两种使用方式：

- 自动托管模式：测试使用默认本地地址，即 `REGISTRY_URL=http://localhost:9001`、`CA_URL=http://localhost:9003`、`DISCO_URL=http://localhost:9005`、`MQ_GROUP_API_URL=https://localhost:9007`、`MQ_AUTH_API_URL=https://localhost:9008`；当这些地址在测试启动时不可达，`tests/_local_services.py` 才会按测试模式受管启动所需兄弟服务。
- 手工托管模式：测试启动时，如果你配置的目标地址已经可达，无论它们是不是默认端口，测试都会直接复用这些已运行服务，不会再拉起新的 sibling 进程。
- 自定义地址模式：如果你把上述环境变量改成了非默认地址或端口，测试会把它视为“由你自己托管的目标环境”；此时即使目标服务不可达，夹具也不会代你启动，而是直接报错，要求你先把这些自定义目标服务启动好。

区分规则可以收敛成一句话：先看“测试要访问的 base_url 是不是默认本地地址”，再看“这个地址在测试开始时是否已经可达”。只有“默认本地地址 + 当前不可达”这一种组合，才会进入自动托管。

补充说明：当前自动托管并不是“随机端口模式”。`tests/_local_services.py` 受管启动的仍然是固定默认端口 `9001/9003/9005/9007/9008`，只是由 pytest 进程代替你手工执行各兄弟仓库的 bootstrap/start。

如果你选择手工托管服务，建议先执行：

```bash
just doctor
```

`just doctor` 会把 registry / ca / discovery / mq 四个服务一起检查；对 MQ 来说，它会优先使用 `[mq]` 配置、`bootstrap-artifacts/`，或本地 `../mq-auth-server/certs/` 中的 probe 证书材料。

为了让 `integration` / `e2e` / `just test` 更稳定：

- `registry-server` 联调时建议使用 `APP_ENV=development CA_SERVER_MOCK=false`，以启用真实 CA 吊销通知链路。
- `registry-server` 与 `ca-server` 需要使用同一个 `REGISTRY_SERVER_INTERNAL_API_TOKEN`。
- `mq-auth-server` 的 `9007` / `9008` 要求 mTLS；如果测试涉及 `admin mq ...`，需要提前准备可用客户端证书。

### 3.3. 运行测试与约定

推荐执行顺序：

1. 在 `acps-cli` 仓库执行 `just dev bootstrap`。
2. 如需运行测试，再执行 `just test bootstrap`。
3. 若要手工联调或使用自定义服务地址，按第 2.3 节启动本地联调实例。
4. 回到 `acps-cli` 执行 `just doctor`，确认 registry / ca / discovery / mq 四个目标都可达。
5. 若使用默认本地地址且这些地址当前还没有服务监听，可直接运行 `just test integration`、`just test e2e` 或 `just test`；测试夹具会自动托管所需兄弟服务。
6. 若你已经手工启动了默认端口服务，或者通过环境变量改成了自定义地址，则测试只会复用这些现有目标；尤其是自定义地址不可达时，测试不会自动补拉服务。

`just doctor` 的角色是对手工联调前置条件做集中检查；如果它失败，优先修复启动矩阵、端口、token 或证书问题。对默认本地地址的测试路径，`tests/_local_services.py` 会在这些默认地址不可达时按测试模式受管启动兄弟服务，因此不再要求 `just test integration` / `just test e2e` 先显式通过 `doctor`。如果你改成了非默认地址，`doctor` 和测试都只会检查/使用你指定的那些目标，不会自动回退到本机默认端口。

当前稳定基线：

- 在 README 约定的联调启动方式下，`just test` 应能跑通 unit / integration / e2e。

当前仍允许保留 `skip` 的场景，应限于明确标注的未来工作，例如尚未实现的多实例 discovery forwarder/fallback 联调与 fanout 聚合能力；凡是由于环境未准备而触发的 `skip`，都属于待清理对象，而不是长期设计目标。

## 4. 通用打包与部署

`acps-cli` 不参与 `release-app` / `standalone` 里的应用容器部署，也不是常驻服务；它只需要一个原生 Python wheel 运行包，供宿主机上的运维、provision 和联调脚本直接调用。与 server 仓库不同，本章只说明通用 wheel 运行包，不涉及 systemd 安装。

`acps-infra/scripts/release-standalone/install.sh` 中的 `write_infra_cli_conf()` 已经给出了一套稳定的 bootstrap 语义：在运行目录写入 `acps-cli.toml`，并把 token 工作目录收口到 `./.acps-cli/tokens/`。本仓通用运行包沿用这一思路，但额外保留 Discovery 与 MQ 的默认配置。

### 4.1. 构建运行包

执行前置条件：

- 执行环境需要在 `PATH` 中提供 `just`、`uv` 和 `python3` 命令。
- 如果构建机还没有可用的 `python3`，推荐先用 `uv` 安装 Python 3.14，并创建一个共享的 `.venv`；激活后 `python3` 会指向这个虚拟环境，多个兄弟项目可以共用它来构建。

假设你在`~/acps-build`下准备构建环境：

```bash
mkdir -p ~/acps-build
cd ~/acps-build
uv python install 3.14
uv venv --python 3.14 .venv
source .venv/bin/activate
python3 --version
```

克隆本仓库：

```bash
cd ~/acps-build
git clone <acps-cli 仓库地址>
```

执行打包：

```bash
cd ~/acps-build/acps-cli
just package wheel
just package wheel offline
```

打包说明：

- `dist/acps-cli-wheel-{version}-{platform}.tar.gz` 是在线运行包。
- `dist/acps-cli-wheel-offline-{version}-{platform}.tar.gz` 是离线运行包。
- 文件名中的 `{platform}` 表示目标部署平台：默认使用当前构建机平台；如果显式传入 `--pip-platform`，则使用该值。
- 两种运行包都会包含以下运行时必需文件和目录：
  - `dist/`：包含当前版本的应用 wheel 文件。
  - `acps-cli.toml`：运行时默认配置文件。
  - `.env.example`：环境变量模板。
  - `README.md`：随包交付的部署说明文档。
  - `requirements-runtime.txt`：运行时依赖清单。
  - `checksums.txt`：运行包内容校验清单。
  - `scripts/bootstrap.sh`：为 `registry-server:9002`、`mq-auth-server`、`rabbitmq`、`redis`、`demo-partner` 和 `demo-leader` 申请部署态证书的统一入口。
  - `scripts/acs/`：静态 ACS 申请材料目录；部署前需要按目标环境手工修改。
  - `scripts/smoke-test-business.sh`：运行包目录内可直接执行的跨服务业务烟测脚本。
- 离线运行包还会额外包含：
  - `wheelhouse/`：预下载的 Python 运行时依赖 wheel 目录，用于离线安装。

```bash
just package wheel offline \
  --pip-platform manylinux2014_x86_64 \
  --pip-platform manylinux_2_28_x86_64 \
  --pip-implementation cp \
  --pip-abi cp314
```

离线包说明：

- 这里的“离线”仅指 CLI 本体和运行时依赖已随包提供；它不包括 Python 本身。
- 目标服务、数据库、RabbitMQ、最终签发后的证书文件，以及 `acps-infra` 的其它组件都不在该包内。
- `just package wheel offline` 默认按当前构建机平台下载 wheel；如果目标机平台不同，请显式传入 `--pip-platform`、`--pip-implementation` 和 `--pip-abi`。
- `--pip-platform` 可重复传入。对 Linux 目标来说，部分依赖会同时使用 `manylinux2014` 与更新的 `manylinux_2_28` 标签；例如 x86_64 目标通常应同时传 `manylinux2014_x86_64` 与 `manylinux_2_28_x86_64`。
- `--pip-implementation` 和 `--pip-abi` 的值需要与目标机 Python 版本匹配，例如 Python 3.14 对应 `cp` 和 `cp314`。

### 4.2. 目标机部署

原生部署前请自行准备目标服务地址、账号凭证、证书目录和本地工作目录；这些能力不再由 Docker、`acps-infra` 或各个 server 仓库代管。

如果目标机尚未安装 Python 3.14，可以用 `uv` 命令或者其它方式安装 Python 3.14。命令：`uv python install 3.14 --install-dir /opt/uv-python --no-bin` 会把 Python 3.14 安装到 `/opt/uv-python/`，但不创建全局可执行链接；这样对目标机系统环境影响更小，也避免了与系统 Python 的版本冲突。

```bash
mkdir -p /opt/acps-cli
cd /opt/acps-cli
tar xzf acps-cli-wheel-offline-{version}-{platform}.tar.gz

# 注意：压缩包会解出一层同名根目录，后续命令应进入该目录执行
cd acps-cli-wheel-offline-{version}-{platform}

# 创建虚拟环境；python 3.14 的路径根据实际安装位置调整
/opt/uv-python/cpython-3.14.x-<platform>/bin/python3.14 -m venv .venv

# 在线安装：同一条命令同时安装锁定的运行时依赖和应用 wheel
.venv/bin/python -m pip install \
  -r requirements-runtime.txt \
  dist/acps_cli-{version}-py3-none-any.whl

# 如果目标机无法访问公网，则改用下面这组离线安装命令；不要与上面的在线命令重复执行

# 离线安装：同一条命令同时安装锁定的运行时依赖和应用 wheel
.venv/bin/python -m pip install \
  --no-index \
  --find-links wheelhouse \
  -r requirements-runtime.txt \
  dist/acps_cli-{version}-py3-none-any.whl

# 可选：如后续需要环境变量覆盖，可先准备 .env 占位文件
cp .env.example .env

# 编辑 acps-cli.toml，确认目标服务地址
# 至少检查 [registry]、[ca]、[discovery]、[mq] 4 个节中的 base_url / group_api_url / auth_api_url
# 如果需要 auto-register 默认资料，也可在 [registry] 中设置 display_name / org_name

# 为 bootstrap / smoke 预留产物目录（也可让脚本首次执行时自动创建）
mkdir -p bootstrap-artifacts
```

部署说明：

- 如果用 `source .venv/bin/activate` 激活虚拟环境；命令行中的 `.venv/bin/acps-cli` 可简化为 `acps-cli`。
- 如果离线运行包中的 `wheelhouse/` 与目标机平台不匹配，请回到构建机重新执行 `just package wheel offline ...`。
- `acps-cli.toml` 中的相对路径会相对于配置文件所在目录解析，因此 `./.acps-cli/`、`./keyfiles/` 这类工作目录可以稳定跟随运行包根目录一起迁移。
- 当你显式传入 `--config /path/to/acps-cli.toml` 时，CLI 也会优先加载该配置文件同目录下的 `.env`，因此如后续确有环境变量覆盖需求，也不需要依赖当前 shell 的工作目录。
- Registry 用户名/密码默认不再写入 `.env`；建议在命令行中显式传入，或直接使用交互式提示输入。
- 首次执行 `scripts/bootstrap.sh` 前，必须先按目标环境手工修改 `scripts/acs/*.json`；尤其确认 `certificate.altNames` 里的对外 DNS/IP，必要时连同 `name`、`provider` 一并调整到实际部署值。
- `scripts/acs/*.json` 是明确的证书申请材料；`bootstrap.sh` 只会原样读取并复制快照到 `bootstrap-artifacts/`，不会再根据 `acps-cli.toml` 生成/追加 SAN，也不会回写 `aic`。
- 如果目标环境已经按 `acps-infra/scripts/release-standalone/install.sh` 完成安装，可直接复用其生成的 `runtime/acps-cli.toml` 作为 bootstrap 配置；它已经写好了 Registry / CA 地址与 token 文件目录。若你还需要 Discovery / MQ 命令，再手工补上 `[discovery]` 与 `[mq]` 两个节即可。
- `acps-cli` 本身不会启动任何后端服务；本章只负责把工具安装到目标机，并约定好配置、凭证和工作目录。
- `scripts/bootstrap.sh` 和 `scripts/smoke-test-business.sh` 会把中间产物与证书材料收口到 `bootstrap-artifacts/` 下，不再依赖你手工维护一整套临时 token / keyfiles 目录。

### 4.3. 使用方式与功能验证

以下步骤默认假定：

- `registry-server` 已按其 README 第 4 章完成第一阶段部署，`9001` public plane 已可用，但 `9002` 还未切回双端口。
- `ca-server` 已完成部署并可用。
- `discovery-server` 已按目标环境配置启动。
- `mq-auth-server` 的运行目录已准备好，但证书目录还未通过 ACPs 体系下发。
- 当前 `acps-cli.toml` 已指向目标环境实际使用的 Registry / CA / Discovery / MQ 地址。

#### 4.3.1. 第一步：执行 bootstrap.sh 申请部署证书

先在 `acps-cli` 所在机器执行统一证书自举脚本：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh all --config ./acps-cli.toml
```

如果 `registry-server` 与 `mq-auth-server` 安装目录都在本机可访问文件系统，可以直接让脚本自动落盘到目标 `certs/`：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh all \
  --config ./acps-cli.toml \
  --registry-install-dir /opt/registry-server \
  --mq-auth-install-dir /opt/mq-auth-server
```

补充说明：

- 若未通过参数或环境变量提供凭据，脚本会交互式提示输入普通用户和管理员账号密码。
- 普通用户凭据与管理员凭据应使用两个不同的 Registry 账号；当前 Registry 会在同一用户记录上覆盖 access token，若复用同一账号先后执行 user/admin 登录，前一 token 会失效，`agent save` 这类后续步骤可能返回 `401 Could not validate credentials`。
- `bootstrap.sh` 会把中间 token、EAB、证书文件和 ACS 快照全部收口到 `./bootstrap-artifacts/`，不会污染运行包根目录的 `keyfiles/`。
- 运行前请先手工修改 `scripts/acs/*.json`；特别是 `registry-server-9002-service-acs.json`、`mq-auth-server-acs.json` 中的对外 DNS/IP。脚本不会再根据 `acps-cli.toml` 生成或追加 SAN，也不会改写这些 JSON。
- 运行完成后，`./bootstrap-artifacts/summary.json` 会汇总所有 AIC、文件路径与分发结果（自动落盘或手工拷贝建议）。
- `--registry-install-dir` 与 `--mq-auth-install-dir` 仅适用于服务安装目录在本机可访问的场景；若服务不在本机（例如独立主机部署），不要传这两个参数，继续按手工拷贝流程分发证书。

生成的目录合同如下：

- `bootstrap-artifacts/registry-server-9002/`：`registry-server-9002-service-acs.json`、`registry-server-9002-probe-acs.json`、`server.pem`、`server.key`、`trust-bundle.pem`、`client.pem`、`client.key`
- `bootstrap-artifacts/mq-auth-server/`：`mq-auth-server-acs.json`、`healthcheck-client-acs.json`、`server.pem`、`server.key`、`client.pem`、`client.key`、`acps-root-ca.pem`

如果需要为独立部署的 RabbitMQ / Redis 准备基础设施证书，可分别执行：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh rabbitmq --config ./acps-cli.toml
bash scripts/bootstrap.sh redis --config ./acps-cli.toml
```

如果 `rabbitmq` 或 `redis` 的安装目录在本机可访问，也可以让脚本直接把证书写入对应 `<install-dir>/certs/`：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh rabbitmq \
  --config ./acps-cli.toml \
  --install-dir /opt/stage-infra

bash scripts/bootstrap.sh redis \
  --config ./acps-cli.toml \
  --install-dir /opt/stage-infra
```

对应目录合同如下：

- `bootstrap-artifacts/rabbitmq/`：`rabbitmq-acs.json`、`rabbitmq-server.pem`、`rabbitmq-server.key`、`rabbitmq-client.pem`、`rabbitmq-client.key`、`acps-root-ca.pem`
- `bootstrap-artifacts/redis/`：`redis-acs.json`、`redis-server.pem`、`redis-server.key`、`acps-root-ca.pem`

#### 4.3.2. 第二步：把 bootstrap 产物分发到各服务主机

默认情况下，`bootstrap.sh` 只负责申请证书并把文件落在自己的受管目录里；由于 `acps-cli` 和各服务可能不在同一台机器上，证书文件需要由你自己复制到各目标应用的运行目录。

如果在 4.3.1 已传入 `--registry-install-dir` / `--mq-auth-install-dir`，对应证书会自动写入目标 `<install-dir>/certs/`，本步骤可跳过对应服务。

推荐拷贝关系：

- 复制到 `registry-server` 主机：
  `bootstrap-artifacts/registry-server-9002/server.pem -> /opt/registry-server/certs/server.pem`
  `bootstrap-artifacts/registry-server-9002/server.key -> /opt/registry-server/certs/server.key`
  `bootstrap-artifacts/registry-server-9002/trust-bundle.pem -> /opt/registry-server/certs/trust-bundle.pem`
- 保留在 `acps-cli` / 运维机上，用于后续 `9002` 烟测：
  `bootstrap-artifacts/registry-server-9002/client.pem`
  `bootstrap-artifacts/registry-server-9002/client.key`
  `bootstrap-artifacts/registry-server-9002/trust-bundle.pem`
- 复制到 `mq-auth-server` 主机：
  `bootstrap-artifacts/mq-auth-server/server.pem -> /opt/mq-auth-server/certs/server.pem`
  `bootstrap-artifacts/mq-auth-server/server.key -> /opt/mq-auth-server/certs/server.key`
  `bootstrap-artifacts/mq-auth-server/client.pem -> /opt/mq-auth-server/certs/client.pem`
  `bootstrap-artifacts/mq-auth-server/client.key -> /opt/mq-auth-server/certs/client.key`
  `bootstrap-artifacts/mq-auth-server/acps-root-ca.pem -> /opt/mq-auth-server/certs/acps-root-ca.pem`
- 复制到 RabbitMQ 主机或 stage-infra 运行目录：
  `bootstrap-artifacts/rabbitmq/rabbitmq-server.pem -> <rabbitmq-install-dir>/certs/rabbitmq-server.pem`
  `bootstrap-artifacts/rabbitmq/rabbitmq-server.key -> <rabbitmq-install-dir>/certs/rabbitmq-server.key`
  `bootstrap-artifacts/rabbitmq/rabbitmq-client.pem -> <rabbitmq-install-dir>/certs/rabbitmq-client.pem`
  `bootstrap-artifacts/rabbitmq/rabbitmq-client.key -> <rabbitmq-install-dir>/certs/rabbitmq-client.key`
  `bootstrap-artifacts/rabbitmq/acps-root-ca.pem -> <rabbitmq-install-dir>/certs/acps-root-ca.pem`
- 复制到 Redis 主机或 stage-infra 运行目录：
  `bootstrap-artifacts/redis/redis-server.pem -> <redis-install-dir>/certs/redis-server.pem`
  `bootstrap-artifacts/redis/redis-server.key -> <redis-install-dir>/certs/redis-server.key`
  `bootstrap-artifacts/redis/acps-root-ca.pem -> <redis-install-dir>/certs/acps-root-ca.pem`

#### 4.3.3. 第三步：按各服务 README 完成证书切换与启动

证书文件分发完成后：

- 按 `registry-server` README 第 4 章，把 `9002` 切回启用状态，并让 `.env` 指向刚复制过去的 `server.pem`、`server.key`、`trust-bundle.pem`。
- 按 `mq-auth-server` README 第 4 章，让 `.env` 与运行目录中的 `certs/` 指向刚复制过去的 5 个证书文件，再启动或重启服务。
- 确保 `registry-server:9001`、`registry-server:9002`、`ca-server:9003`、`discovery-server:9005`、`mq-auth-server:9007/9008` 都已进入目标运行状态。

#### 4.3.4. 第四步：执行统一业务烟测

证书和服务都准备好之后，用运行包自带的业务烟测替代手工命令链：

```bash
cd /opt/acps-cli
bash scripts/smoke-test-business.sh --config ./acps-cli.toml --bootstrap-dir ./bootstrap-artifacts
```

`scripts/smoke-test-business.sh` 会覆盖以下主干路径：

- CLI 入口是否可执行
- Registry 用户登录、保存 ACS、提交审核、管理员审批
- CA 的 EAB 获取与 `clientAuth` 发证（并校验证书主题包含目标 AIC）
- Discovery `status`、Agent `sync`、DSP `hard-reset/sync/status` 轮询
- Discovery `query` 轮询，直到命中本次烟测创建的 Agent AIC
- `registry-server:9002` 的 mTLS `/health`
- `mq-auth-server` 的 `health`
- MQ 的 `group add-member -> auth-probe allow -> group delete -> auth-probe deny`

可以把这套业务烟测理解为“单次最小闭环”：

- Registry 负责把 Agent 从“提交”推进到“审批通过”
- CA 负责把审批得到的 AIC 转成可用证书
- Discovery 负责把已审批 Agent 同步到检索面并可被查询命中
- MQ 负责验证 ACL 写入后可放行、删除后会拒绝

补充说明：

- 若未显式提供管理员凭据，烟测脚本会交互式提示输入；普通用户凭据由脚本自动生成并隔离，不需要你手工准备。
- 脚本会在执行结束时自动清理本次创建的 smoke Agent，避免长期污染 Registry/Discovery 数据。
- 烟测汇总会写到 `bootstrap-artifacts/smoke-test-summary.json`。
- `bootstrap.sh` 负责申请与整理证书；`smoke-test-business.sh` 负责验证服务主干功能；两个脚本职责分离，不再混用。

#### 4.3.5. 第五步：为 demo-partner 安装目录执行原地 bootstrap

除了上面的 `all` 聚合模式，`bootstrap.sh` 还支持面向 `demo-partner` 安装目录的独立模式：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh demo-partner \
  --config ./acps-cli.toml \
  --install-dir /opt/demo-partner
```

补充说明：

- 这个模式不会读取 `scripts/acs/` 下的静态模板，而是直接扫描 `--install-dir/partners/online/*/acs.json`。
- 脚本会对这些 `acs.json` 逐个执行保存、提交、审批和发证，并把审批返回的 `aic` 回写到原文件。
- 证书文件会直接写回各自的 Partner 子目录：`server.pem`、`server.key`、`trust-bundle.pem`、`client.pem`、`client.key`。
- 默认汇总目录是 `<install-dir>/bootstrap-artifacts/`；如需改到其它目录，可显式传入 `--output-dir`。
- 因为该模式需要直接读写安装目录，所以 `acps-cli` 必须与 `demo-partner` 位于同一台机器，或至少能访问同一个共享文件系统。

#### 4.3.6. 第六步：为 demo-leader 安装目录执行原地 bootstrap

除了上面的 `all` 聚合模式与 `demo-partner` 模式，`bootstrap.sh` 还支持面向 `demo-leader` 安装目录的独立模式：

```bash
cd /opt/acps-cli
bash scripts/bootstrap.sh demo-leader \
  --config ./acps-cli.toml \
  --install-dir /opt/demo-leader \
  --partner-install-dir /opt/demo-partner
```

补充说明：

- 这个模式不会读取 `scripts/acs/` 下的静态模板，而是直接读取 `--install-dir/leader/atr/acs.json`。
- 脚本会对这个 `acs.json` 执行保存、提交、审批和发证，并把审批返回的 `aic` 回写到原文件。
- 如果可以访问 `demo-partner` 的运行目录，脚本还会把 `leader/scenario/expert/tour/china_hotel.json` 与 `china_transport.json` 整文件同步为 `demo-partner` 的对应 ACS，避免静态 AIC 漂移；也可以显式传 `--partner-install-dir` 指定源目录。
- 证书文件会直接写回 `leader/atr/`：`client.pem`、`client.key`、`trust-bundle.pem`。
- 默认汇总目录是 `<install-dir>/bootstrap-artifacts/`；其中 `demo-leader/summary.json` 会记录 AIC、`acs.json` 路径和证书文件路径。
- 因为该模式需要直接读写安装目录，所以 `acps-cli` 必须与 `demo-leader` 位于同一台机器，或至少能访问同一个共享文件系统。

`acps-cli` 不是常驻服务，因此本仓没有与 server README 对应的 `4.4 systemd 安装与启停` 小节；完成上面的验证后，直接把 `.venv/bin/acps-cli` 加入你的运维脚本或 shell PATH 即可。
