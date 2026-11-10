"""归属识别的受控分层补读；读取背景不增加本人候选槽位。"""
from pathlib import Path

from navigation_access import freeze
from task_identification import canonical
from runtime import digest

MAX_ROUNDS=24


def complete(page, root, access, client, receive):
    root=Path(root);current=access.start(page)
    freeze(root/'input.json',dict(page_sha256=digest(page),navigation=access.identity))
    for index in range(MAX_ROUNDS+1):
        path=root/str(index)
        record=client.identify(current,path/'identify.receipt.json');value=receive(record)
        nodes=value.get('needs_nodes',[])
        if not nodes:
            canonical(current,value)
            freeze(root/'context.json',current)
            return current,value
        if (set(value)!={'materials','goals','needs_nodes'} or value['materials']!={} or value['goals']!=[]):
            raise ValueError('归属补读不能同时提交本人或目标判断')
        if index>=MAX_ROUNDS:raise ValueError('归属分层补读达到执行上限，原回执保留待核')
        next_page=access.expand(current,nodes,str(index))
        freeze(path/'request.json',dict(previous_page_sha256=digest(current),
            previous_response_sha256=digest(record),next_page_sha256=digest(next_page),nodes=nodes))
        current=next_page
    raise AssertionError('归属补读未到达终态')
