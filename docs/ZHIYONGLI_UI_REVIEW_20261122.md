# 每日分析、周评分与列表操作

已确认：每日继续评审，仅展示四维分数、能力图、任务及原文依据；每周综合五个工作日的已核材料给出周评分。日评的缺失维度保持为空，不补零、不因覆盖多加分。页面说明每日分析与周评分的节奏。保留历史结果及回执，恢复原能力图的非等距刻度与缺失状态。

## 接口依赖清单

| 页面与动作 | 方法路径 | 关键参数 | 实际消费字段 | 状态与验证 | 下一责任方 |
| --- | --- | --- | --- | --- | --- |
| 我的智用力切换日/周及日期 | GET /api/v1/zhiyongli/me/reports | period_type,page,page_size | items,total,page,page_size,period_start | 联调通过；生产每日与周切换均HTTP200，个人19日四维与空周状态已核 | 当前任务 |
| 个人或管理员打开分析、能力图和任务依据 | GET /api/v1/{zhiyongli/me,admin/zhiyongli}/reports/:id | id | dimensions,items,daily_review,weekly_review,score,score_status | 日评联调通过；liyu四维、lican单维能力图及原文抽屉已核。新版周评真实有分报告待发布后验证 | 当前任务 |
| 管理端报告筛选、排序、跳页 | GET /api/v1/admin/zhiyongli/reports | period_type,period_start,publication_status,search,page,page_size,sort | items,total,page,page_size | 联调通过；生产P_desc跨页数值顺序与缺分置后、跳末页、改排序回第一页已核，真实HTTP200 | 当前任务 |
| 评审进度筛选、排序、跳页及刷新 | GET /api/v1/admin/zhiyongli/jobs | state,search,page,page_size,sort | items,total,page,page_size,state,active,updated_at | 联调通过；生产email_asc第3页HTTP200，连续自动刷新保持排序与页码，顶部吸附分页已核 | 当前任务 |
| 接入记录跳页 | GET /api/v1/admin/zhiyongli/runs | page,page_size | items,total,page,page_size | 联调通过；生产顶部跳至第3页显示41至60/457，runs?page=3&page_size=20返回200 | 当前任务 |
| 日评规则阅读与下载 | 前端随版本打包的Markdown | version=1.6,period_type=day | 日评材料、维度分析、新版周评分说明 | 生产规则页已展示每日材料范围、每日分析与新版周评分说明；下载入口未操作验证 | 当前任务 |

`sort`在报告列表接受period_desc/period_asc、email_asc/email_desc、P/M/J/E各自的_asc/_desc；非日评另接受score_asc/score_desc。日评拒绝按隐藏日总分排序。评审列表接受空值（既有执行及问题优先）、updated_desc/updated_asc、period_desc/period_asc、email_asc/email_desc。未知键返回现有400错误。服务端白名单拼接固定SQL表达式；所有筛选、本人权限、完整结果排序在LIMIT/OFFSET之前执行；分值按数值排序，缺失置后，同值以日期/ID稳定排序。前端不做当前页本地排序。

分页复用现有公共Pagination，只在智用力列表接入，不修改公共组件；顶部分页吸附在应用64px标题栏下，保留底部入口和跳页，默认每页20条。排序变更回第一页，异步旧响应不能覆盖新查询。

本次不触及审计采集、模型转发、服务密钥、落账去重、费用、用户数据或全局表格。部署时核对健康与真实采集/用量增长；“未修改公共链路”本身不等于生产验证通过。

新增接口缺口为0；TODO(接口联调)及接口暂未支持类warning为0。最终验收须补充真实Network、页面、截图证据，不将本地回归当成已联调完成。

## 2026-11-22 生产验收记录

- 界面部署a5b0a47d3，周期框宽度修订ef4858524。最后部署回执app_healthy，2026-11-22T06:32:31Z健康200，无在途模型请求，日评Worker恢复。
- 桌面Chrome截图：`.dev/ui-personal-daily-final.jpg`（完整四维、能力图、日总分隐藏、每日分析完整文案）；`.dev/ui-lican-one-dimension.jpg`（缺失维度不补零）；`.dev/ui-jobs-sticky-pagination.jpg`（顶部吸附排序分页）。
- 真实Network：个人day列表/报告157及week列表均200；报告P_desc与评审email_asc跨页200；接入记录第3页200。列表筛选、排序由服务端完成，无本页本地排序。
- 业务观察：采集最大ID从4330622增至4331375，用量4179344增至4180188，落账4184954增至4185798（北京时间14:44:11）；健康200。此前14:02核对自首轮界面上线后的fingerprint冲突为0。这些证据覆盖本次观察窗口，不宣称所有AIHub功能全量验收。
- 六项依赖中，四项已联调通过；报告详情的新版周报告以及规则下载仍有未验证分支，不宣称整个日周链路联调完成。新增接口契约缺口0；本次TODO(接口联调)清理0、剩余0；接口暂未支持类warning清理0、剩余0。

## 周综合分资格修正

生产周评前检查发现新版周评误用了日评的已观察维度权重归一；改为既有正式规则的四维均有依据才形成综合分。Python与Go导入端同时约束，汇总版本`weekly-complete-dimensions-20261122.2`。缺整维保留已有分项、维度及原文，状态observation_only、总分为空，不重新分配缺失维度权重。并未因为维度覆盖多而额外加分，未改历史日评、旧周/月报告。
