# 每日分析、周评分与列表操作

已确认：每日继续评审，仅展示四维分数、能力图、任务及原文依据；每周综合五个工作日的已核材料给出周评分。日评的缺失维度保持为空，不补零、不因覆盖多加分。页面说明每日分析与周评分的节奏。保留历史结果及回执，恢复原能力图的非等距刻度与缺失状态。

## 接口依赖清单

| 页面与动作 | 方法路径 | 关键参数 | 实际消费字段 | 状态与验证 | 下一责任方 |
| --- | --- | --- | --- | --- | --- |
| 我的智用力切换日/周及日期 | GET /api/v1/zhiyongli/me/reports | period_type,page,page_size | items,total,page,page_size,period_start | 既有接口；待生产页面复验 | 当前任务 |
| 个人或管理员打开分析、能力图和任务依据 | GET /api/v1/{zhiyongli/me,admin/zhiyongli}/reports/:id | id | dimensions,items,daily_review,weekly_review,score,score_status | 既有接口及新版周评字段；待生产真实页面复验 | 当前任务 |
| 管理端报告筛选、排序、跳页 | GET /api/v1/admin/zhiyongli/reports | period_type,period_start,publication_status,search,page,page_size,sort | items,total,page,page_size | 新增明确sort合同；本地真实PG数值排序、缺失最后、跨页不重复通过，待生产HTTP与页面验证 | 当前任务 |
| 评审进度筛选、排序、跳页及刷新 | GET /api/v1/admin/zhiyongli/jobs | state,search,page,page_size,sort | items,total,page,page_size,state,active,updated_at | 新增sort；本地真实PG筛选与分页通过，待生产HTTP与页面验证 | 当前任务 |
| 接入记录跳页 | GET /api/v1/admin/zhiyongli/runs | page,page_size | items,total,page,page_size | 既有接口；待生产页面复验 | 当前任务 |
| 日评规则阅读与下载 | 前端随版本打包的Markdown | version=1.6,period_type=day | 日评材料、维度分析、新版周评分说明 | 测试通过，待生产页面复验 | 当前任务 |

`sort`在报告列表接受period_desc/period_asc、email_asc/email_desc、P/M/J/E各自的_asc/_desc；非日评另接受score_asc/score_desc。日评拒绝按隐藏日总分排序。评审列表接受空值（既有执行及问题优先）、updated_desc/updated_asc、period_desc/period_asc、email_asc/email_desc。未知键返回现有400错误。服务端白名单拼接固定SQL表达式；所有筛选、本人权限、完整结果排序在LIMIT/OFFSET之前执行；分值按数值排序，缺失置后，同值以日期/ID稳定排序。前端不做当前页本地排序。

分页复用现有公共Pagination，只在智用力列表接入，不修改公共组件；顶部分页吸附在应用64px标题栏下，保留底部入口和跳页，默认每页20条。排序变更回第一页，异步旧响应不能覆盖新查询。

本次不触及审计采集、模型转发、服务密钥、落账去重、费用、用户数据或全局表格。部署时核对健康与真实采集/用量增长；“未修改公共链路”本身不等于生产验证通过。

新增接口缺口为0；TODO(接口联调)及接口暂未支持类warning为0。最终验收须补充真实Network、页面、截图证据，不将本地回归当成已联调完成。
