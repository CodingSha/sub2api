"""008上下文相关性候选：沿用PMJE既有判据及校验，不修改冻结版本。"""
import copy

from . import candidate_review_coverage as previous
from .common import require

VERSION = 'pmje-observation-20261103.14.9-context1'
FEEDBACK_VERSION = previous.FEEDBACK_VERSION
PERFORMANCE = previous.PERFORMANCE
ITEMS = previous.ITEMS
RUBRIC = copy.deepcopy(previous.RUBRIC)
RUBRIC['version'] = VERSION

INSTRUCTIONS = previous.INSTRUCTIONS + '''
本版仅澄清P2输入相关性，沿用既有状态、quality、performance和fact_coverage，不新增评分层或数值规则，不预设人员应有分差。此前候选的其他语义争议未因此解决。
还原当前输入时，区分当次请求明确携带的内容、能可靠重建的客户端有效上下文、仅在同会话历史/评测索引中可查的内容。session_id相同、source_group相同、评测包提供linked_context或可补读，均不证明当时业务模型实际看过旧内容。必要时补读允许的原文核对携带方式和来源；无法重建的有效上下文如实说明，不凭历史可查推定污染，也不因此把其他可评部分一律改未知。
同属项目不证明需求相关，不同项目也不证明无关。判断旧内容是否为当前必要公共背景、真实依赖或不适用的旧需求/版本/临时约束，依据语义关系和当前切换/输入行为，不能按项目名、任务数、输入长度、轮数或工具量判。明确切换适用范围、摘要保留公共背景、隔离旧约束均可合理；不要求一律新开对话，新对话本身也不自动高档。已明确隔离的不适用内容不能仅因仍可查而判低。
P2可归责不足须定位：确实被带入或可靠重建仍有效的具体无关内容、当前本人可控的输入/边界处置、当时合理可行的隔离方式和实际组织缺口。平台自动保留、屏幕包装、自动压缩、审计聚合、AI自行跑偏或评测器装配进去的内容，不能直接归责用户；human_spans只定位本人当次表达，不证明包装是本人主动选择或可控。旧任务内容只能作为context证据，当前输入/切换动作才是本任务本人依据。
对P2已证的非关键上下文组织不足，输入本身的无关混入且边界未妥善处理可以构成actual_gap，使用既有minor_gap/partial；不必先等AI犯错，不虚构误导、额外成本或严重后果。必要缺口、严重失效和关键越界仍各须相应证据，不能只凭抽象风险升级。仅缺高档选择/核对证据不是不足；携带、相关性或本人控制存在决定性未知时，只保留该候选的限制，不能凭session扣分或宣称已排除所有残留。
现有fact_coverage.reason及P2理由简明交代有关候选的携带可信度、相关性、归责和处置；不为没有线索的任务制造逐消息清单。主归P2的同一输入残留缺陷不再跨P1/P3/E1、消息或旧任务数重复扣分；其他项只有独立事实才另评，跨任务不能重复认领同一切换行为。不重新合并无关父任务，不扩大为整会话全文评审。
复杂条件的识别只说明评价条件；本人有处理节点只说明存在可评行为。驾驭表现仍须对照本项既有effective/adapted/verified，解释选择是否相称、实际作用或已证缺口及未验边界，不因复杂、参与或明确隔离就自动升档。
'''


def check(unit, value):
    require(value.get('version') == VERSION, '候选版本错误')
    return previous.check(unit, dict(value, version=previous.VERSION))
