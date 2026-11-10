# 智用力当前评审执行入口

当前用户规则：[手册1.6](../../docs/ZHIYONGLI_SCORING_RULES_V1_6.md)。语义合同：`scoring_pipeline/weekly_performance/contract-instructions.txt`与`contract-rubric.json`，版本`contextual-review-20261109.1`；目标合同：`scoring_pipeline/goal_policy.py`。分值尺023与语义合同分别追溯。

本地先由当前Codex阅读相关对话，按固定判据完成语义评审JSON，顶层写入实际采用的`review_policy`。来源、反证、恢复、范围及未决均须说明，不能只添加版本字段沿用未复核旧判断。程序不代替语义判断，以下命令不调用模型API。

在仓库根目录运行：

```sh
python3 research/ai-usage-assessment/run_scoring.py week --input INPUT.json --review REVIEW.json --expected-sources SOURCES.json --output NEW_RESULT.json
python3 research/ai-usage-assessment/run_scoring.py month --help
```

周命令默认要求1.6语义声明。仅重放历史1.5判断时显式传`--rules-version 1.5`，输出仍标历史版；更早的旧runner须显式`run_scoring.py legacy ...`，已有`behavior-v7`至`behavior-v11`路由保留。月命令确定性复用已核期间，不能用模型自由判月档。

产品包按既有已核导出链执行`export_results.convert`或`export_reviewed.month_report`，再`qualify_results.py --input ... --output ...`。默认1.6；只有原评审均声明当前语义合同才传播`policy_versions.review`，缺声明拒绝转换。1.4/1.5显式版本仍可复现。四维资格、同版正式周、月内片段、资料处置及精确算术继续由Go独立检查。规则声明不等于模型语义真实性已经自动核验。

`runtime.py`为后端集成持久队列，默认不调用API。2026-11-10用户已授权沿用公司AI Hub专用模型，完成全员评分流程所需请求无需逐批申请次数；后台仍须显式启用`--company-api`。请求预算用于工程流控，保留实际用量和原回执；旧队列的源签名、执行合同及语义核准不能因代码升级自动重用为新评审。结果不明不重发。月分的确定性计算不调用模型；后台编排中的周与月内片段仍需语义评审。

旧输出、冻结规则及来源引用保留，禁止把历史个人分数作为当前评分模板。未授权的全量重评、重新抓数、生产变更和发布不属于上述命令执行范围。

## 后台任务入口（本轮接入验收中）

`worker.py`消费管理员登记的持久任务，使用`pipeline.py`处理来源、模型识别、评审、同模型另次语义检查和确定性导出。人工发布仍由现有管理员接口执行。当前真实评分验收未通过，不能当作已完成的生产自动化入口。详细状态见[自动评审账本](../../docs/ZHIYONGLI_AUTOMATION_V1.md)。

用户已于2026-11-10明确授权公司AI Hub `deepseek-v4.1-flash-local`，为完成全员AI使用评分流程自行发起所需请求，不再逐批申请次数。请求总量、流控和熔断仍保留工程限额及账簿，生产变更与员工结果发布边界不变。本地Codex直接评审入口继续保留。后台默认禁用模型请求，显式传`--company-api`才启用；专用模型配置通过`ZHIYONGLI_MODEL_CONFIG`指定，不读取Codex凭证。产品任务库和只读来源库分别由`ZHIYONGLI_DB_DSN_FILE`、`ZHIYONGLI_SOURCE_DSN_FILE`注入私有连接文件；`ZHIYONGLI_WORKER_KEY_FILE`只允许任务候选导入。既有直接DSN环境变量仍支持，文件优先。

```sh
python3 tools/zhiyongli/worker.py --state /absolute/private/state
python3 tools/zhiyongli/worker.py --state /absolute/private/state --company-api --once --exclude-api-key-id REVIEW_KEY_ID --max-requests-per-job 3 --flow-config /absolute/private/flow.json
python3 tools/zhiyongli/worker.py --state /absolute/private/state --healthcheck
```

第一条只返回禁用状态，不连接数据库或模型。第二条要求部署环境已配置、flow.json包含已确认专用配额且显式排除评审流量；示例3次仅为历史烟测工程限额，不是完整周期预算；当前不在生产运行。预算按持久回执计数，重启不重置。模型请求前冻结输入和合同，已收回执从原输入重验，结果不明不重发；恢复校验不代表发起新评审。源码或来源变化会暂停，不能直接改旧回执或签名绕过。明确拒绝后的修订重评及人工争议处理尚未形成完整管理页面入口。

单次请求采用保守容量预算，长来源逐页读取并校验连续字符覆盖；分页同时检查引用结构和JSON格式后的请求体。已接入自动分层导航、分批本人归属、跨批目标语义归并及完整候选分段。分段不是新消息或任务，原消息数与执行片段数单列。完整原生材料与有界模型视图分开；原生校验、资格、导出使用完整来源集合，模型引文还须来自实际提供过的原文。评分和另次复查分别受控补读，访问链及共享结果绑定签名。超过容量或执行预算仍明确待核，不裁切原文。月编排保留周和截止前月内片段；长月必要缺口按项核对各期间依据并另次检查，月分汇总本身不调用模型重判。长周和带恢复处置的长月已通过对应合成主路径，完整真实周期、评分语义和百人实际吞吐未验收，不得默认填成无机会或已恢复。证据见[本轮离线报告](../../outputs/zhiyongli-bounded-pipeline-offline-20261109/report.md)。

容器准备见[部署说明](../../docs/ZHIYONGLI_AUTOMATION_DEPLOY.md)。本地镜像已构建，非root、只读rootfs容器的空来源链已验证；完整真实评分和百人吞吐仍未验收，未生产部署。

另次语义复查可自行请求同一冻结来源的事件或导航节点原文。`audit_readback.py`单独保存复查材料及每轮回执，不改写原评分输入；最终复查及逐项处置绑定整条链，分歧阻断，unknown不重发。相同模型的另次检查仍非独立人评。该能力未解除自动分层归属和全周有界综合的发送前保护。

## 离线容量测算

最新[批次与请求测量验收](../../outputs/zhiyongli-bounded-pipeline-offline-20261109/measurement-report.md)保留24/48/96片段的完整原文对照，生产仍用24片段。`operations.py`只读输出阶段耗时/输入输出token覆盖及完整/截断/失败分布、超期与积压诊断；缺失保持未知，旧回执不回填，不发外部通知。合成通过不证明真实模型吞吐、完整周/月判断或生产配额；当前不满足上线条件。

当前工作日来源分布、请求包装对照及放行缺口见[工作日材料负载复核](../../outputs/zhiyongli-bounded-pipeline-offline-20261109/workweek-load-report.md)。阅读引用按连续字符分段，所有换行及原始偏移保留，合同版本为`bounded-source-reading-20261109.4`；旧回执和unknown不升级或重发。该改动主要降低传输结构量，不证明百人实际评分可按时完成。

`capacity_plan.py --input deploy/zhiyongli-capacity.example.json --output /absolute/private/result.json`不连接模型或数据库，只模拟明确输入的用户周期阶段、延迟、滚动配额和累计预算。假设与实测来源必须声明，输出绑定输入签名；预算暂停与超期不能算完成。例子只有两个合成用户，不是生产配置；百人8场景、缓存计数和当前缺口见[容量报告](../../outputs/zhiyongli-capacity-offline-20261109/report.md)。没有已确认配额或完整评分路径时，合成通过不能作为上线通过。Compose须显式填写`ZHIYONGLI_MAX_REQUESTS_PER_JOB`，不默认使用3次小样本预算。
