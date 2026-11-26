"""候选评审规则：固定现有PMJ及数值，只收敛E3到实际Skill/个人工作流。"""
from copy import deepcopy
from runtime import RUBRIC as BASE_RUBRIC

VERSION='task-evidence-review-20261124.3'
RUBRIC=deepcopy(BASE_RUBRIC)
RUBRIC['e3_scope_version']='skill-personal-workflow-20261124.1'
RUBRIC['performance_anchors']['E3']={
    'reasonable':'本人合理选择或明确采纳Skill、个人工作流或可重复执行流程，并在当前任务中实际使用。普通文件、网页、模板版式或文案参考不属于本项。',
    'good':'实际使用的Skill或个人工作流准确适配当前任务，能依据执行痕迹说明减少重复说明、免于重新组织步骤或可靠接续的具体作用。不能仅凭名称或工具调用存在。',
    'outstanding':'在80基础上，流程适用条件、必要输入保留与实际免重复或可靠接续作用有充分可核对应。仅选择Skill、未来建立流程或AI自述成功不足以推出。',
    'exceptional':'在90基础上，当前流程复用的准确适配、必要信息保全及免重复作用特别简洁可靠且有具体正证。不要求用户自建、亲自执行、他人采用或自己统计节省量。',
}
RUBRIC['item_counterexamples']['E3']=(
    '读取文档、网页URL、PDF或按参考文件版式/文案润色属于材料提供和使用，不能只凭这一点计E3。'
    '通用CLI/MCP存在、AI自主选用、代码模块复用、角色分工不自动是用户工作流能力。'
    '可认可无Skill名称的既定多步骤个人流程，但必须有本人选择或明确采纳、当前实际执行和任务提效依据。'
)
