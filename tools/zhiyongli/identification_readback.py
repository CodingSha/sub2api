"""归属识别的受控分层补读；读取背景不增加本人候选槽位。"""
from pathlib import Path

from navigation_access import freeze
from task_identification import canonical,checked
from runtime import digest

MAX_ROUNDS=24


def complete(page, root, access, client, receive):
    root=Path(root);current=access.start(page)
    freeze(root/'input.json',dict(page_sha256=digest(page),navigation=access.identity))
    for index in range(MAX_ROUNDS+1):
        path=root/str(index)
        def check(value,page=current,stage=str(index)):
            if not value.get('needs_nodes',[]):return canonical(page,value)
            if set(value)!={'materials','goals','needs_nodes'} or value['materials']!={} or value['goals']!=[]:
                raise ValueError('归属补读不能同时提交本人或目标判断：请求补读时materials须为{}、goals须为[]；给出判断时needs_nodes须为空')
            if int(stage)>=MAX_ROUNDS:raise ValueError('归属分层补读达到执行上限：须基于已提供内容给出判断，不再请求节点')
            # 节点请求不合合同或超容量时同样带原因有界重发，不直接终止整周；成功展开才留存证明。
            return access.expand(page,value['needs_nodes'],stage)
        record,value,next_page=checked(client,current,path/'identify.receipt.json',receive,check)
        nodes=value.get('needs_nodes',[])
        if not nodes:
            freeze(root/'context.json',current)
            return current,value
        freeze(path/'request.json',dict(previous_page_sha256=digest(current),
            previous_response_sha256=digest(record),next_page_sha256=digest(next_page),nodes=nodes))
        current=next_page
    raise AssertionError('归属补读未到达终态')
