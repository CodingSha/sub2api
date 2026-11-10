# v14候选执行与验收

当前候选不是生产放行。语义合同见[行为观察手册](../56-observation-manual-v14.md)，发布条件见[生产候选计划](../55-production-candidate-plan-20261103.md)。实际结果以运行目录的`manifest.json`、`queue.json`、原判与回执为准。

当前执行版本为`observation-batch-20261103.8`。PMJE语义仍为14.4；新增`outcome-observation-20261103.1`独立成果合同，按[用户确认范围](../58-pmje-and-outcome-release-contract-20261103.md)单列成果质量，不计入能力总分。两个入口显式传递合同，初始化自动冻结实际模块源码，不修改旧批次。58项成果合同、原执行及客户端回归通过；不据此宣称模型语义或生产验收通过。

历史`.7`仅增加错误定位，44项回归与76份历史输出离线回放通过。详见[该轮执行验收](../../../outputs/zhiyongli-v14-execution-audit-20261103/report.md)。旧版本不使用新代码续跑，旧模型指标不伪装成`.8`新评结果。

## 执行入口

在`research/ai-usage-assessment/`执行。`RUN`须为新的运行目录；此处名称仅为命令示意，不是现有成功结果。

```sh
python3 -m scoring_pipeline.stable_batch --run RUN init --dataset DATASET --keys KEYS --purpose diagnostic
python3 -m scoring_pipeline.stable_batch --run RUN run --workers 4
python3 -m scoring_pipeline.stable_batch --run RUN status
python3 -m scoring_pipeline.stable_batch --run RUN verify
```

`KEYS`是任务键JSON列表；省略时处理冻结索引中全部单元，受限单元只记隔离，不发送模型。程序调用已配置的公司模型，不使用聊天中的手工评分或子Agent。

成果观察使用独立的新目录，将上述模块名改为`scoring_pipeline.outcome_batch`，其余命令相同。PMJE入口不能打开成果运行，成果入口也不能打开PMJE运行；报告与回执按各自版本保存。成果观察没有能力数值，不进入PMJE聚合。

## 停止与恢复

```sh
python3 -m scoring_pipeline.stable_batch --run RUN stop
python3 -m scoring_pipeline.stable_batch --run RUN resume
python3 -m scoring_pipeline.stable_batch --run RUN run --workers 4
```

停止信号阻止新任务和后续模型步骤；正在传输的请求可能仍结束并留存。再次运行时核对已成功结果和期间回执，仅继续未完成队列。一个运行目录只允许一个写入进程。

`retry-failed`只重新排入标记可重试的传输失败，不重排政策拒绝、合同失败、源文件漂移或成功结果。合同失败先查保存的调用与错误，不能反复调用直到拿到喜欢的结论，也不能把执行失败记成员工未知或低分。

## 冻结与追溯

初始化记录来源索引、共享原文、语义说明、量表、实际执行模块、模型配置及SHA。改变冻结模块后不允许续跑旧目录；保留原目录、冻结源码快照与诊断，使用新目录明确记录修复版本。不得改旧manifest签名绕过检查。

每期间独立按记录时间截断；缺独立完成时间的助手回复正文隔离。当前本人输入、历史本人背景与其他任务背景分别保留角色。补读只能来自已许可、早于期末的集合。程序验证连续原句、期间、来源类别及合同，不能验证原句在语义上一定支持结论。

`parts/`保存期间原判及调用轨迹；`reviews/`只装配显式期间结论，不增加一次虚构的整任务模型复核；`receipts/`对照来源和末次原判；`calls/`保留模型输出与传输失败记录。原始缓存共享使用，不按每轮复制全量原文。

## 结果解释和发布

挑战、本人行为B1—B4、处理质量、实际机会及未知分别统计。行为档不假设等距，不平均为个人能力总分或旧L等级；处理质量不是独立成果质量维度。模型自报和同模型重复不是业务人评。

新版本先经过针对性回归、真实案例语义核查、冻结诊断及独立请求重复；未过预先声明闸门不扩大全量。最终报告必须展示真实完成范围、全部未完成及失败、重复差异和发布未通过项。用途、人评、正式能力分值有效性与工程可运行分别验收，不能因程序跑完而宣布可发布生产。
