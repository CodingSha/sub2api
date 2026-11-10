# 智用力后台评审部署准备

2026-11-10发布阻断更新：来源顺序修复后的48片段原反例回归通过，24片段请求出现输出失败；完整周候选还暴露主体、机会和归责判断错误，四页长请求并发测试均未收到终态，正在定位。详见[完整周期验收进展](../outputs/zhiyongli-bounded-pipeline-offline-20261109/real-cycle-acceptance-report.md)。用户已授权全员评分流程所需模型请求，无需逐批申请次数；旧七次限制仅保留历史。**以下前序归档和Worker镜像未包含最新修复，不能作为当前代码的待上线版本。** 最终仍须完整真实周期、百人实际吞吐、来源负载与对应源码/镜像恢复验收，当前不执行生产发布。

2026-11-10当前发布候选以[源码归档与镜像核对](../outputs/zhiyongli-bounded-pipeline-offline-20261109/source-candidate-report.md)及恢复r6为准；两镜像均由同一归档构建。原归档不因后续证据文档更新而改写，最终发布须复核清单；本次未执行生产命令或新增真实模型调用。

2026-11-09。当前为准备说明，**尚未满足上线验收，未在生产执行下列命令**。本地最小权限、启动/恢复/空来源页、合成候选接入和人工发布、原生合成月包、启用容器空来源链已验证；完整真实长周/月和评分语义仍未通过，见[实施账本](ZHIYONGLI_AUTOMATION_V1.md)。沿用RayToken的版本/理由追溯、DeepEval/G-Eval的固定合同、Langfuse/Opik的运行回执思路；不引入这些项目依赖，不将模型自检当作真人校准。

当前容量依据见[稳定阅读分页与容量复核](../outputs/zhiyongli-bounded-pipeline-offline-20261109/reading-dates-report.md)：216项Python含本地PG通过，最新镜像空来源容器0模型/0导入。来源日期变化处结束阅读页，减少日常重复工作并保留全部原文和顺序；封口新增量未下降，16个重算纯阅读假设场景不能替代百人实际吞吐。6小时调休窗口、完整真实周期和专用配额仍须验收；本地测试通过不改变本页的停止部署状态。

## 运行组件与权限

沿用Go/Gin、Vue、PostgreSQL与现有管理员认证，新增一个Python Worker。自动调度按受控配置启动，管理员按钮保留补跑；Worker只读采集、写任务阶段，通过专用接口接入候选；管理员核对后发布，不自动发布。

以上为当前已实现入口。用户最新确认恢复原工作日运行计划，正常周周五24:00截止、周六02:00封口启动、周一08:00前准备候选；目标包含日常准备与自动启动，但仍保留人工发布。2026法定日历、工作日范围、日常准备及自动调度已接入代码并通过本地合成/数据库检查，真实完整周/月及百人吞吐尚未验收。默认调度关闭，不启用旧自然周任务继续评分，见[73号决定](../research/ai-usage-assessment/73-workday-scale-restoration-20261109.md)。

- 数据迁移：既有`241_zhiyongli_reports.sql`加`245_zhiyongli_jobs.sql`、`246_zhiyongli_workday_scope.sql`。242至244保留给旧自动化工作区，未搬回旧规则。
- 任务库账户只需读取任务及账户、更新任务阶段；报告写入走Go导入接口。迁移使用部署管理员身份，不能让Worker自行迁移。
- 来源库使用独立只读账户，按`deploy/zhiyongli-permissions.sql`授予所需列的SELECT，默认只读；采集已从整行JSON改为显式列投影。程序另以REPEATABLE READ READ ONLY事务防写。本地权限已实际验证，生产未执行。核实实际主机/数据库，不能只根据localhost判断环境。
- 来源采集按人定位，在同一只读事务内以服务端游标连续分页；仅非空会话标识补取同人前文，空/NULL标识的当期记录仍保留。共享SQLite缓存的首次WAL/表结构初始化以目录文件锁互斥。周/月/日常入口遇到旧查询版本快照时保留待核，不直接续评分。224项Python含本地PG及百人四连接合成验证通过；生产只读探针的单批耗时不替代整周期采集负载验收，详见[来源查询报告](../outputs/zhiyongli-bounded-pipeline-offline-20261109/source-query-report.md)。
- 必须排除评审专用API Key ID，防止评审流量再次成为员工材料；白名单账户同时从任务启动和采集排除。
- 个人评分另有独立排除范围：2026-11-10负责人确认Jarvis为公共/自动化账户，生产只读核验ID为22。发布配置明确设置`ZHIYONGLI_EXCLUDED_USER_IDS=22`，应用与Worker必须使用同一值；多账户用逗号分隔正整数ID，不允许重复、空项或非法值。正式发布层缺失或空值时拒绝解析。自动日常准备/周月入队、手动启动、来源快照、旧任务续跑、候选导入和发布均限制该范围；历史来源、回执和结果保留。它不修改`audit_whitelist_users`，因此不影响AI Hub审计采集。
- Worker Key仅允许内部调度`POST /api/v1/internal/zhiyongli/schedule`和`POST /api/v1/internal/zhiyongli/jobs/:id/import`，只能按服务端已确认范围建任务或接入当前执行任务对应的人员、周期与版本，不具有发布或管理员其他权限。未配置或私有文件检查失败即拒绝。

## 私有文件与持久目录

在仓库外准备两个绝对路径，分别作为`ZHIYONGLI_PRIVATE_DIR`、`ZHIYONGLI_STATE_DIR`。目录0700，普通文件0400或0600。容器内Worker与既有后端均用UID 1000；共享Worker Key的属主需允许两个进程读取，不能通过放宽为全员可读解决。Worker状态目录由UID 1000写入。

私有目录包含：

| 文件 | 内容 |
| --- | --- |
| `model.json` | 由`deploy/zhiyongli-model.example.json`创建，HTTPS公司端点及专用模型 |
| `model.key` | 模型专用Key，不读Codex登录凭证 |
| `worker.key` | 至少32字符的随机接入凭证，与后端共享 |
| `product.dsn` | 任务库PostgreSQL URI |
| `source.dsn` | 来源库只读PostgreSQL URI |
| `schedule.json` | 从`deploy/zhiyongli-schedule.example.json`填写；默认disabled，启用须指定首周及有效管理员ID，不接收Worker自填人员或周期 |
| `flow.json` | 从`deploy/zhiyongli-flow.example.json`填写已确认的模型专用配额、累计预算及冷却策略；示例空值不能运行 |
| `database-budget.json` | 从`deploy/zhiyongli-db-budget.example.json`填写实际PG实例归属及各服务额度；未知额度保留null，预算检查不会通过，不填入DSN或凭证 |

不得把这些文件或原文缓存写入Git、镜像或公开输出。模型与导入端点拒绝重定向；禁止把凭证转发至重定向目的地。Worker状态含来源原文、模型响应、引用和回执，应作为个人数据控制访问。

## 本地容器验收入口

以下组合仅用于本地容器验收，不作为生产发布入口：

```sh
docker compose -f deploy/docker-compose.dev.yml -f deploy/docker-compose.zhiyongli.yml --profile zhiyongli config --quiet
docker compose -f deploy/docker-compose.dev.yml -f deploy/docker-compose.zhiyongli.yml --profile zhiyongli build zhiyongli-worker
docker compose -f deploy/docker-compose.dev.yml -f deploy/docker-compose.zhiyongli.yml --profile zhiyongli up -d sub2api zhiyongli-worker
```

## 生产发布入口与连接预算

2026-11-10从运行容器标签核实：生产项目为`deploy`，工作目录为`/data/sub2api/deploy`，原组合是`docker-compose.local.yml`和`docker-compose.override.yml`，Compose版本2.27.1。必须保留这两个文件及服务器原环境；不能用开发组合替换，不能因名称含local而把它当成本机测试库。已用标准输入传入新增两层的等效配置，与生产原组合执行`config --quiet`成功；使用占位镜像摘要和路径，不创建目录、不拉取或启动镜像。最终实际镜像、私有文件与四文件组合仍须核实。

追加`docker-compose.zhiyongli.yml`和`docker-compose.zhiyongli.release.yml`。发布层要求分别填写应用的`ZHIYONGLI_APP_IMAGE_REPOSITORY`/`ZHIYONGLI_APP_IMAGE_SHA256`及Worker的`ZHIYONGLI_WORKER_IMAGE_REPOSITORY`/`ZHIYONGLI_WORKER_IMAGE_SHA256`，组成不可变的`仓库@sha256:摘要`；明确指定生产已核实的linux/amd64平台并禁止自动拉取。Mac默认架构镜像、可变latest标签、空来源烟测和本地构建成功均不能代替目标平台完整验收。发布前从已核准渠道装载镜像并核对实际摘要、架构及执行合同，当前尚未批准这一对正式镜像。

本地配置检查只证明Compose解析及层间合并；不证明镜像存在、摘要真实或容器能够运行。发布层保留基础Worker的build定义便于本地构建，生产命令必须用`--no-build --no-deps`，仅替换应用和新增Worker，避免重建或更新PostgreSQL、Redis。下列检查命令**尚未在生产执行**；服务器已只读核实Python3.6.8及UTF-8环境。先在Bash中检查实际四层配置及连接预算，完整配置只经管道传给检查器，不落盘或回显；任一步非0即停止：

```sh
set -e
set -o pipefail
cd /data/sub2api/deploy
docker compose -p deploy -f docker-compose.local.yml -f docker-compose.override.yml -f docker-compose.zhiyongli.yml -f docker-compose.zhiyongli.release.yml --profile zhiyongli config --quiet
docker compose -p deploy -f docker-compose.local.yml -f docker-compose.override.yml -f docker-compose.zhiyongli.yml -f docker-compose.zhiyongli.release.yml --profile zhiyongli config --format json |
  python3 check_zhiyongli_db_budget.py --plan "$ZHIYONGLI_PRIVATE_DIR/database-budget.json" --compose-json - --output "$ZHIYONGLI_PRIVATE_DIR/database-budget-preflight.json"
```

预算结果包含声明的实例、应用池、Worker连接及未知额度；超过上限或存在未知额度返回2，结构/数值非法也返回非0。即使返回0也只证明所声明的连接预算可容纳，输出仍为`production_ready=false`，不能代替模型配额、真实周期或发布审批。检查结果应按本轮私有验收记录保留；复核前先归档已有同名结果，不覆盖失败证据。只有全部上线验收和数据库目标、迁移、发布范围获准后才执行：

```sh
cd /data/sub2api/deploy
docker compose -p deploy -f docker-compose.local.yml -f docker-compose.override.yml -f docker-compose.zhiyongli.yml -f docker-compose.zhiyongli.release.yml --profile zhiyongli up -d --no-build --no-deps sub2api zhiyongli-worker
```

应用启动会运行尚未登记的迁移，不能把上述up当作只增加Worker。2026-11-10已全量核对生产295份既有迁移，恢复本地缺失的生产同SHA文件`239_audit_whitelist_users.sql`；291份完全一致、4份匹配代码原有历史兼容规则，只有241/245/246三份智用力迁移待执行。见[核对与空库验收](../outputs/zhiyongli-bounded-pipeline-offline-20261109/production-release-contract-report.md)。这不证明生产schema、数据及备份恢复已验收；实际发布前须重核最终镜像的迁移清单，完成目标备份和对应恢复验证后单独批准。不能仅凭最大文件名证明所有旧迁移一致，也不能覆盖服务器已有override。

生产当前`DATABASE_MAX_OPEN_CONNS=256`、`DATABASE_MAX_IDLE_CONNS=128`，而PG的`max_connections=100`、`superuser_reserved_connections=3`、`reserved_connections=0`。这些是实际配置，不是当前已建立256连接。Worker每线程持有任务库和来源库各1条连接；同一PG实例下合计2条，HTTP调度/导入消耗已有应用连接池，不另算一套连接池。若为同一实例，须按`应用连接预算 + 2×Worker线程数 + 其他服务及运维连接预算 ≤ 97`核定上限；来源/任务库分处不同实例时逐实例分别核算，不能仅按DSN字面比较。

现有256应用配置单独已超过97，**连接预算尚未闭环，不能以观察时只有6个连接作为扩并发依据**。应用池调整、其他服务额度、PG扩容或独立实例均须结合实际服务负载确认；本轮不擅自减小应用池或提高PG上限。即使模型配额允许32线程，额外64连接也须纳入同一预算，不能直接部署。详情见[生产发布契约核查](../outputs/zhiyongli-bounded-pipeline-offline-20261109/production-release-contract-report.md)。

发布追加层现要求显式提供`ZHIYONGLI_APP_DB_MAX_OPEN_CONNS`及`ZHIYONGLI_APP_DB_MAX_IDLE_CONNS`，分别映射后端既有`DATABASE_MAX_OPEN_CONNS`/`DATABASE_MAX_IDLE_CONNS`。缺失或空值时Compose拒绝解析，不能再继承256/128；非数字、最大值为0、空闲数大于最大数须由上述预算检查拒绝。没有预设生产推荐值；64/16及4线程仍是测算候选，实际额度尚未批准。14项离线专项（含真实Compose解析）通过；随后在服务器原两文件上以当前候选标准输入等效层复核通过，原依赖/持久化/无关环境保留。探针私有路径、Key ID及3次预算为占位，未上传最终四文件或改变生产设置；实际变量和最终四文件仍需核验。详见[连接池发布检查验收](../outputs/zhiyongli-bounded-pipeline-offline-20261109/db-pool-release-report.md)。

设置`ZHIYONGLI_EXCLUDED_USER_IDS=22`、`ZHIYONGLI_REVIEW_API_KEY_ID`与两个私有目录变量，并依据核准容量显式填写`ZHIYONGLI_MAX_REQUESTS_PER_JOB`；缺少预算时Compose拒绝启动，不再默认使用3次小样本预算。`ZHIYONGLI_WORKERS`默认1，不能只配置模型并发却忽略执行线程；线程数须在1至32之间且不超过已确认模型并发。组合文件使用现有`sub2api-network`，Worker不对外开放端口；后端需使用包含245/246迁移及新增接口的本次源码镜像。构建本身不等于数据库、模型或页面联调通过。

### 主机就地构建方式（2026-11-10）

生产只读核实的实际发布形态是“`/data/sub2api`为git检出，`docker-compose.override.yml`在主机上从仓库根构建`sub2api-local:latest`”，没有镜像仓库。与此一致的发布路径是切换分支后在主机构建，回退即还原旧镜像并切回`codex/shenji`。为此新增：

- `docker-compose.zhiyongli.host.yml`：与local、override、zhiyongli三层合用；Worker镜像固定为`zhiyongli-worker-local:latest`，应用和Worker都必须显式给出`ZHIYONGLI_EXCLUDED_USER_IDS`。不改应用既有连接池设置，上文连接预算未闭环的结论不变。
- `zhiyongli-host-deploy.sh`：`sudo bash zhiyongli-host-deploy.sh <阶段>`，各阶段可重复执行，任一步失败即停。

| 阶段 | 做什么 | 是否写生产 |
| --- | --- | --- |
| `preflight` | 核对项目容器、库名、账户数、连接数、已有智用力迁移、评审Key状态、磁盘 | 只读 |
| `prepare` | 建`/data/zhiyongli/{private,state,backup}`；生成`worker.key`；评审专用Key从本库直接写入私有文件，不经终端；写`model.json`（`http://sub2api:8080`）、`schedule.json`（disabled）、`flow.json`、`compose.env`；四层`config --quiet` | 只写新目录 |
| `backup` | `pg_dump`（不含`llm_audit_logs`数据，三份迁移不改该表）压缩并校验 | 只写备份目录 |
| `build` | 给当前运行镜像打`sub2api-local:rollback-pre-zhiyongli`，再构建应用与Worker镜像 | 新镜像，未替换容器 |
| `app` | `up -d --no-build --no-deps sub2api`，启动时执行241/245/246迁移 | **替换应用、执行迁移** |
| `grants` | 执行`zhiyongli-permissions.sql`，为两个角色生成口令并写DSN私有文件 | **新增角色与授权** |
| `worker` | `--profile zhiyongli up -d --no-build --no-deps zhiyongli-worker` | 新增容器 |
| `status` | 容器、任务状态分布、运行账目摘要（`status full`为完整输出） | 只读 |
| `rollback` | 停Worker，旧镜像重新标为latest，按原两层重启应用；新增表、任务、回执、状态卷保留 | 还原应用 |

须设置`ZHIYONGLI_REVIEW_API_KEY_ID`；`ZHIYONGLI_WORKERS`默认2，`ZHIYONGLI_ROOT`默认`/data/zhiyongli`。回退后再次发布须先重新`build`，`app`阶段会拒绝把旧镜像当新版本启动。回退后源码另行切回原分支。

2026-11-10在本机用一次性Compose项目（独立空库、非生产）在Linux容器内以root逐阶段演练一遍：九个阶段全部按预期结束；`app`后登记到三份迁移；管理员经真实HTTP登记上周全员周评审，3个任务由Worker读取来源后落为`no_activity`；`rollback`后应用回到原镜像且任务记录保留；回退后直接`app`被拒绝，重新`build`后可再次发布，重复执行`grants`后Worker仍能领取并完成任务。**演练未覆盖**：生产数据量下的构建耗时与备份体积、从shenji镜像首次执行三份迁移（演练基线镜像已含）、Worker经`http://sub2api:8080`的真实模型请求、linux/amd64主机构建。这些只能在生产首次执行时逐阶段核对。

组合文件没有默认任务请求预算，必须显式配置`ZHIYONGLI_MAX_REQUESTS_PER_JOB`。此前3次仅为小样本接入验收预算，不适用于完整周。全量运行预算需核对实际分段规模，不能用预算截断后宣称评审完成。独立CLI默认100份，可显式指定更低预算。每次发送前保存回执；超时、断流和结果不明默认不自动重发，仅在`flow.json`显式配置`max_interrupted_retries`后按上限续发（见下）。

API执行器默认每轮最多1个新请求，可用`ZHIYONGLI_NEW_REQUESTS_PER_TURN`配置1至20。新发送先记任务账簿，预算跨轮及重启保留；旧任务保守继承既有回执。已存回执重验、跨任务共享阅读不计新发送；发送前账簿已有预留但回执缺失时保守视为unknown，不能换任务绕过。

`flow.json`是启用API执行器的必要配置，不提供猜测的生产配额。同一Worker状态卷内统一约束并发、RPM、保守token预留及累计请求预算；`budget_key`标识一次执行预算的范围，不因重启重置。2026-11-10用户已明确授权全员评分流程所需的公司模型请求，无需逐批申请次数；累计上限仍用于防失控与核账，不作为重复申请用户授权的门槛。token预留按请求UTF-8字节加最大输出计算，属于保守工程预算，不是实际token统计；端点返回usage时另记实际量。额度超限等待；单请求超过已配预算或累计预算用尽明确暂停。429尊重Retry-After并冷却，连续unknown触发熔断；unknown默认不自动重发。`max_429_retries`默认为0，可显式配置0至3；仅明确HTTP429允许冷却后续跑，原拒绝及每次尝试单独留存，全部计入任务/全局预算。跨任务待发送尝试记录原账簿，预留后无回执保守unknown；连续429耗尽后待核。8项合成端点回归通过，尚未以真实端点证明全天无人值守恢复。

`max_interrupted_retries`（2026-11-10新增）默认为0，可显式配置0至3。仅对客户端没有取得终态的传输中断生效：流未到终态即结束、流超时、连接重置或提前关闭、HTTP 408/500/502/503/504，以及持锁核对时仍为`requesting`的已退出进程遗留回执。原unknown回执不改写，硬链接为`<name>.attempt-N.receipt.json`逐次留存，`<name>.receipt.json`成为续发链入口；每次续发都计入任务与全局预算，仍受并发、RPM和熔断约束。次数用尽后落为unknown，管理员恢复仍为409。发送前已预留但没有回执的情况始终是unknown，不续发。服务端可能已经处理了被中断的那次请求，续发意味着同一请求最多多消耗配置次数的模型用量；它不改变评分输入，也不替代对入口断流原因的排查。12项合成回归通过；本地经公网入口的真实小周中，一次评分请求连续中断3次后第4次取得完整结果，原回执均保留。

断流定位（2026-11-10，生产只读）：按网关请求ID核对生产应用日志，上述被中断的前两次请求在应用侧都正常处理到结束（HTTP 200，耗时173.6秒和216.6秒），而客户端分别在约80秒和约12秒时就收到了没有终态的流结束。中断发生在客户端与应用主机之间的入口链路或本机网络，不在应用和模型上游；应用在客户端断开后仍读完上游，这两次请求的模型用量已实际产生。入口响应带阿里云`acw_tc`与`Server: nginx`；生产主机上唯一的nginx容器属于Dify，不代理AI Hub，应用端口由主机8180直接暴露。具体由哪一层切断尚未查明，需要有入口层（负载均衡/WAF/前置代理）权限的人核对同时段记录。同日一份40页长周的33次阅读请求经同一入口全部一次完成，中断表现为按时段聚集而不是固定时长超时。生产Worker的模型端点配置为容器网络内的`http://sub2api:8080`，不经过该入口；续发只作为残余中断的兜底。

Worker取得同状态卷独占锁后先核对遗留active预留，不依赖可能在容器重建后复用的PID。原回执已有终态时接回结果，缺失或仍requesting时将预留记为unknown并保留预算及原回执；启动核账本身不重新发送，遗留的`requesting`回执之后是否续发只取决于`max_interrupted_retries`。两个断网容器实际复用PID=1的检查通过。启动核账不是已核清模型侧请求，unknown仍需按原记录处理。

可用`ZHIYONGLI_WORKERS`启用同进程的多个执行线程，默认1且不得超过已确认并发；各线程独立连接任务库及来源库，共用持久配额和PG任务锁。不是跨服务器限流器，禁止多个独立状态卷共享同一专用配额。DSH尚未接入该全局配额和请求轮转，只允许一个执行线程，不能作为百人生产默认方案。

状态卷`read-pages`按账户ID、邮箱、完整单页来源与实际读取请求合同寻址。追加材料的稳定页面可复用；文字、角色、时刻、来源SHA或读取合同变化则失效。原回执只存一份，任务保存签名引用；缺失或损坏阻断，不能当作缓存未命中重新请求。换合同/换任务不能绕过旧unknown；全周目标归并与评分仍按本次完整上下文执行，单页阅读缓存不等于每日语义增量已经完成。

阅读版本20261110.5在来源已保存时刻的北京时间日期变化处结束当前页，保持原片段顺序，不补造未知历史时间，也不按日期切任务。每页24片段及实际请求体预算仍生效；同日新增/修订可能重排同日页面，不能保证零重读。旧计划及回执保留，执行版本变化不升级旧评分；原unknown在分页变更后仍须核账。

只读运维状态入口：`python /app/tools/zhiyongli/operations.py --state /state --flow-config /run/zhiyongli/flow.json`。它使用只读事务输出任务状态数、最老等待、当前1.6待处理/超期/缺截止任务数、模型终态分布、实际usage覆盖和冷却信息，不返回员工正文或模型请求正文。`measurements`按当前预算范围、请求阶段及完整/截断/失败等终态分列客户端单调时钟耗时和输入/输出token；缺失保持null，旧卷缺表明确缺失，不用重启核账间隔替代API延迟。`alerts`返回超期、unknown、预算、冷却与心跳等诊断代码，不向外部发送通知。心跳也包含线程数和全局等待原因；心跳不能证明评分质量或按期完成。测量与容量边界见[本轮验收](../outputs/zhiyongli-bounded-pipeline-offline-20261109/measurement-report.md)。

来源缓存保存在同一状态卷的`source-cache`：完整范围逐行对账修订签名，只对新修订抓正文；任务目录以硬链接引用共享对象，要求同一文件系统。只读稳定事务与SHA检查保留，缓存损坏明确暂停。此机制节省正文网络传输和重复磁盘副本，数据库仍须扫描、计算范围内正文签名；不是CDC，每日来源维护及长材料全读导航已接入，完整周语义判断仍在封口后进行。不得单独清理仍被任务引用的缓存或原执行目录。

100人规模的容量、模型限流、积压监控和故障恢复须按[规模方案与验收条件](ZHIYONGLI_AUTOMATION_SCALE.md)另行验证。历史3次小样本预算、单机心跳或合成100任务轮转不构成生产容量承诺。

用户已确认专用配额暂未知、先做离线测算。可用`capacity_plan.py`及合成配置检查预算、长尾和实际窗口，见[容量报告](../outputs/zhiyongli-capacity-offline-20261109/report.md)。自动长周和月末逐项有界核对已接通合成主路径，见[本轮验收](../outputs/zhiyongli-bounded-pipeline-offline-20261109/report.md)。这些配置不得直接复制为已批准生产配额；完整真实周期、评分语义及百人实际吞吐未验收，生产调度继续关闭。

当前Compose和镜像默认为API执行器，镜像不含DSH运行时。`worker.py --executor dsh --company-api`仅在已核运行时、独立HOME和私有`ZHIYONGLI_DSH_CONFIG`均具备时可用；DSH配置字段为`cli/runtime/home/max_requests/timeout_seconds`，路径必须为绝对路径，不允许使用用户`~/.dsh`。macOS的`.app`不能直接搬入Linux容器；本轮未确认Linux发行包和部署方式，不能据此切换生产默认执行器。CLI仍调用公司模型API，不自动回退、压缩或绕过预算。

DSH每阶段默认最多12次底层发送，Worker仍有整任务总上限；不能把外层CLI次数当API次数。受控工具逐段验签读取、通过`submit_assessment`提交JSON对象，程序仍做原生和语义校验。唯一终态、读取凭据、请求账簿或原文签名不符即阻断。旧版unknown保留，不因新代码能识别预算拒绝就自动升级旧回执。

健康检查验证进程及60秒内心跳，不发模型请求，也不证明模型评分质量或数据库业务链已通过。Worker启动连接失败会退出；任务持久化后可重启续查。SIGTERM等待当前阶段结束，容器保留31分钟停止窗口；强制中止造成请求结果不明时保留unknown。

## 工作日范围与自动调度

唯一日历算法为`tools/zhiyongli/work_calendar.py`；`workday_scope.py`生成`backend/internal/zhiyongli/workday-registry.json`，Python与Go核对同一登记和SHA。周/月键仍为日历期间，实际计分日期、截止、封口、候选准备期限随`workday_scope`冻结。旧自然周任务标记`legacy-natural-week`，新范围另建任务；旧结果不自动升级，新候选发布必须核工作日范围。月024/025日历期间权重和至少3个同版正式周评不改。

自定义公司覆盖先生成登记，再同时配置后端和Worker的`ZHIYONGLI_WORKDAY_REGISTRY`；须挂载同一私有只读文件。历史登记放在受信只读目录，以`<sha>.json`命名，通过`ZHIYONGLI_WORKDAY_REGISTRY_ARCHIVE`保留；不能删除仍被任务引用的旧日历。当前仅核验2026日历，缺年份明确阻断。

调度需要后端`schedule.json.enabled=true`以及Worker `ZHIYONGLI_SCHEDULE_MODE=enabled`两处显式启用；首周及管理员ID须在部署时确认，默认示例不猜。后台每60秒同步，已封口周/月按未删除且非审计白名单的账户自动分批入队，重复同步复用同一任务。整周法定休假保留非计分期间，不生成员工周分。每天02:00后维护本周前一日材料；评审任务优先，空闲时日常只读准备来源和长材料导航，绝不提前出分。调休周按实际最后工作日封口，不保证所有周都有54小时。

`preparations.sqlite`保存待准备日期及进度；执行中到达新日期不会丢失，unknown/待核不因日期更新重置。稳定阅读页及局部导航分支跨日和封口复用，绑定完整原文、人员、时窗、日历及实际请求合同；原文变化即使摘要相同也须重新处理。共享导航原回执在`navigation-branches`保留，正式树引用和树外原回执分别验签。日常完成后保留`prepared-navigation.json`，清理可重建的临时节点库；未完成执行和正式周评依赖不清理。52,703合成叶完整回读及封口导航0次新发送通过，不能承诺真实命中率。每天02:00处理前一日，最后工作日新增量与封口相遇，须计入剩余队列；见[最新容量复核](../outputs/zhiyongli-bounded-pipeline-offline-20261109/navigation-cache-report.md)。调度失败、准备队列和冷却可从只读运维入口观察；当前尚无管理员页面的调度状态面板。任务页工作日日期/封口字段已通过本轮Chrome本地测试管理员的实际展开检查；报告正文也已用带新scope的本地合成候选113核验展开内容与排版，保持draft。

## 周评复用、争议与显式修订

Worker状态根的`shared-weeks`只存签名索引，不复制全量正文。同人员、时窗、全部实际材料与当前代码合同相同才复用；采集封口时间不同但实际材料完全相同可复用。每次核验原文件及语义复查；原目录是引用依赖，不可清理。旧unknown会阻止其他周/月任务换目录或换合同重复发送。

候选未通过另次语义复查，或评分/复查输出未通过程序的确定性校验（引文不逐字存在、字段与状态不一致等）时，API执行器先做有界自动修订（2026-11-10新增）：在`revisions/<签名>`新建执行目录，把上一轮候选、复查未支持的分项意见、须改动候选的跨项问题和校验器给出的原因作为待核材料交回评分，再重新复查；最多2轮。目标识别按相同请求复用原回执，修订轮只新增评分与复查请求，照常计入预算和200,000字节单请求上限。各轮候选、复查回执和`contract-error.json`原样保留，`revision.json`记`automatic_round`；候选通过后报告的限制说明标明经过几轮自动修订。2轮后仍未通过才转`needs_review`。这不是换一次随机结果重评，也不是独立人评；DSH执行器不启用。

复查输出的合同同时收紧：每项先写本人在该项上的实际行为（没有则写“无”）和复查独立判定的状态，再对照候选给结论；独立状态与候选状态不同时，即使结论写了支持，程序也不算通过，并把该项列入修订意见。`global_issues`只写必须改动候选才能解决的跨项问题并阻断通过；`remarks`写不要求改动的范围说明和核对提醒，不阻断，条数写入报告限制说明。评分与复查的输出结构把重复子结构集中到`$defs`引用，约束内容不变，同一小周首轮评分请求体由约198KB降到约160KB。真实小周多次运行显示：同一份材料各次候选的逐项状态仍有明显差异，收紧后的复查对一份“十二项全部记为已观察”的候选指出了4项状态不符，旧合同曾全部放行；这只是一份单输入小周的观察，不代表复查已能稳定拦截全部归责和机会判断问题。

目标识别与跨批归并（2026-11-10新增）：识别、归属分层补读和归并的输出未过程序确定性校验，或输出写满长度上限未完成时，不再直接让整周停在`needs_review`，而是带校验原因对同一输入重发，最多3次；每次回执另存为`*.fixN.receipt.json`，原因写`*.contract-error.json`。结果不明、预算暂停等执行状态仍原样上抛，不进入重发。程序不替模型改归属结论。同时把能写进结构的规则写进结构：非本人槽位的`goal_id`只能为空、`human_quotes`只能为空数组；分层补读标出已展示的叶节点（`leaf_node_ids`），未展开分支只能inspect、未读叶节点只能raw，已提供节点不可再选，并告知剩余容量`remaining_bytes`；评分与复查的原文补读只能请求导航中尚未提供的事件。补读轮也须输出完整归属作为暂定判断但不采用，只认最终不再补读的一轮；这样模型放弃继续补读时也总能给出完整判断。

本人归属未决的处理按手册1.6调整：以用户身份提交、但模型无法确认是否本人表达的输入（打包材料、工具输出、编码数据等）不再阻断整周，已核可评范围照常评审，未决输入不计分、不补数，评审单元记录`attribution_unresolved`及理由，报告限制说明披露条数。整周没有任何已确认本人输入时仍停在`needs_review`。

上述改动由40页长周（用户63，2026-09-21周）逐轮暴露：识别阶段先后出现非本人带目标、补读同时提交判断、对分支请求原文、重复请求、补读超容量、归并目标标识重复、输出退化写满上限，复查修订轮请求已提供事件等失败，每类都曾让整周停下。

管理员页面的“恢复”继续核查已有回执，不处理自动修订用尽后仍存在的评分争议。本地运维入口`tools/zhiyongli/review_control.py`提供：

- `resolution-template`：从实际候选生成未决草稿，所有分项默认未决，不预填通过。
- `activate-resolution`：核验当前输入、原模型/复查回执、逐项理由和真实原句；须有核对者、`human/codex`方式及总理由。通过后仍只生成候选，原模型结果保留，不发布。

语义复查现可自行补读同一冻结来源的事件或导航节点。原评分输入与复查实际材料分开保存，`current-model-audit.json`绑定原评分、原候选、最终复查材料和回执；逐项处置及共享复用同时核验整条补读链。恢复仍不重发unknown，补读仍受原请求预算及120,000字节的复查材料连同候选容量限制。该能力通过合成执行检查，不证明真实完整长周或百人吞吐。
- `revise`：已明确失败/拒绝且不存在unknown、requesting或预算会话时，保存前序签名与原因，新建执行目录；实际请求仍受原任务预算控制。已核候选不能从此入口覆盖。

这些命令必须显式给出`--state`、状态根内的`--directory`，并先停止该Worker；命令与Worker共用进程锁。处理完后再由管理员恢复任务。月内片段须选择实际片段目录；整个自然月不是新的模型重评分对象。不使用此入口改写旧政策签名、抹去争议、谎称独立真人复核或修改已发布报告。

仅明确的DSH发送前预算终态可使用`dsh_client.py --resume-receipt … --reviewer … --reason … --cumulative-limit … --company-api`续接；仍需原配置与HOME。原任务总账簿不可取消，若需提高总预算也必须显式给`--job-cumulative-limit`。每次尝试保留原回执、事件及可变账簿版本，原文asset继续按签名引用。同会话恢复仍可能超上下文或失败，不能当作必然成功。普通unknown不接受此命令。

## 验收与回退

上线前必须实际验证：245/246迁移、数据库权限、并发启动防重、白名单和评审Key排除、来源时窗、unknown默认不重发及显式续发上限、导入幂等、普通用户隔离、人工发布以及整周和跨月全流程。专用测试库限定`127.0.0.1/zhiyongli_test`，测试使用临时schema，不连接生产库。

迁移前完成目标库备份并保留原应用镜像、规则、输入签名、报告、回执与Worker持久卷。回退先停止Worker，保留任务与回执，再还原应用版本；不删除新增表、旧结果或原始来源。已发请求的unknown需先核对，不能通过删除任务目录重跑。生产迁移、部署、回退及数据库恢复仍需另行授权。

状态卷备份需在停止Worker写入后进行，保留源对象与硬链接、索引、原回执、预算、SQLite及可能存在的WAL/SHM；容器内恢复到相同的`/state`绝对路径，避免引用仍指向旧目录。产品库和状态卷须记录业务时点。旧备份之后若又发送过模型请求，不能恢复后直接评分，必须先对照后续回执、任务/全局预算及模型侧记录核账；核不清保留unknown。应用版本回退时保留最新状态卷，不回滚发送预算。本地已验证停写合成状态恢复与0新增发送，尚未验证生产库和状态卷同一时点恢复，见[验收报告](../outputs/zhiyongli-bounded-pipeline-offline-20261109/source-recovery-report.md)。

## 构建输入隔离

构建上下文排除`.dev/`、`outputs/`、`.tmp_review/`、插件密钥和本地环境文件；研究目录仅保留评分程序及等级合同，文档仅保留页面实际导入的法律文件和各版评分手册。目录例外不得重新放入其他分析材料。本轮真实Docker合成上下文验证通过，见[隔离检查](../outputs/zhiyongli-bounded-pipeline-offline-20261109/build-context-report.md)。这只证明新过滤配置下的合成路径；已有候选镜像没有据此重建，最终发布仍须核验源码和镜像对应，不上传整个工作区或员工评审材料。

当前应用/Worker候选的本地合成停写库与状态卷共同恢复演练已通过：298份迁移、待核任务、原回执、已用预算及硬链接保留，恢复后管理员登录/任务HTTP通过，模型新增发送0次。见[当前候选恢复演练](../outputs/zhiyongli-bounded-pipeline-offline-20261109/candidate-recovery-report.md)。这不代表生产备份或实际恢复时点/耗时已验收，生产恢复批准和后续发送核账仍为必要条件。

## 单条长来源与内存预算

当前生产只读观察单条审计正文合计最大约64.6 MiB；不是一周总量。采集已去除重复JSON序列化并改为分块SHA，旧对象格式不变；本地四线程压力假设的冷采集进程峰值降至约1,075 MiB。该值不覆盖完整材料装配/模型阶段，不能乘除后直接当生产内存额度，也不能仅凭模型并发就配置32线程。完整签名探针在整段10秒上限内未完成，生产仍需验证完整采集与数据库/磁盘/网络负载。详见[长来源验收](../outputs/zhiyongli-bounded-pipeline-offline-20261109/source-memory-report.md)。当前Worker候选已重建验签并再次通过本地合成库/状态卷恢复；原版本证据保留，最终生产四层Compose及恢复仍需验收。
