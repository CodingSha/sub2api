"""子目录只用于定位；旧材料不追加新提示词或改变请求身份。"""

DIRECTORY_MODE = 'complete-children-if-fit-20261116.1'
INITIAL_DIRECTORY_MODE = 'initial-root-if-complete-20261116.1'
DIRECTORY_INSTRUCTIONS = '''
child_directory若存在且status=complete，是本轮已展开分支的完整子节点定位目录，按summary、要求、失败反证和未决选择后续分支；它不是原文证据，也不表示已读对应原文。
目录会在下轮访问回收，磁盘历史与已读原文保持；remaining_bytes按可回收目录后的原文空间计算。
status=not_provided_capacity或没有child_directory只表示目录未展示，不能推断无孩子、无事实或无反证；所有available_node_ids仍可按原协议访问。
'''


def instructions(unit):
    access=unit.get('context_access',{})
    text=DIRECTORY_INSTRUCTIONS if access.get('child_directory_mode')==DIRECTORY_MODE else ''
    from source_index import MODE
    if access.get('root',{}).get('source_access_mode')==MODE:
        text+='\n当前为证据优先实验：目录和preview由程序生成，未进行模型首读或语义总结。related_sources仅列同人同会话同请求的原文定位线索，须用raw读取才能作为证据；complete=false时只列部分，其他分支及后续请求仍可能含失败或反证。不能因未读背景而认定无反证，也不能从AI自述认定本人验收。需要的关联原文未提供时先补读，不得猜测评分或目标。'
    return text


def identification_instructions(base, unit):
    """补读轮不要求生成随后丢弃的暂定图，最终轮仍完整核归属。"""
    if not instructions(unit):return base
    previous='请求补读的轮次仍输出完整materials和goals作为暂定判断，但只采用needs_nodes；只有needs_nodes为空的最终一轮形成判断。'
    if previous not in base:raise ValueError('归属补读指令合同变化，须核对新旧规则')
    return base.replace(previous,'请求补读的轮次只提出needs_nodes，materials为空对象、goals为空数组；此时不生成暂定归属或目标，程序也不采用暂定判断。只有needs_nodes为空的最终一轮才完整覆盖全部候选槽位、逐字核本人归属及目标，不沿用未读材料时的猜测。')+instructions(unit)


def discard_directory(unit):
    """仅回收明确新合同的临时目录，不动已读事实或历史观察。"""
    access=unit.get('context_access',{})
    if access.get('child_directory_mode')==DIRECTORY_MODE:access.pop('child_directory',None)
