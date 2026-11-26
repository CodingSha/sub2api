"""局部模型修正不能改写无关分项，合并结果必须可从真实阶段重建。"""
import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
import jsonschema

from conversation_repair import package,apply,with_context
from conversation_transport import encode
from conversation_review import spans
from runtime import digest
from test_conversation_review import task


class ConversationRepairTests(unittest.TestCase):
    def test_workflow_patch_cannot_rewrite_scores_or_other_task_fields(self):
        t=task();t['workflow_index']=[dict(name='push',record_id='tool',chars=5)]
        originals={r['id']:r for r in t['records']};originals['tool']=dict(id='tool',role='tool',text='push')
        data,contract,_=encode([t],spans([t]),originals,[],[],[])
        parent=dict(summary='原整体判断',items=dict(P1=dict(band='good')),
            task_summaries=dict(t=dict(title='原标题',goal='原目标',outcome='原结果',workflow=[])))
        correction=dict(previous_invalid=parent,product_validation_errors=['工具归属无效'],product_tasks_in_scope=['t'])
        packet,schema=package(data,contract,correction)
        patch=dict(items={},summary=None,task_workflows=dict(t=[dict(name='push',purpose='提交',
            selection='AI自主调用',user_refs=[],tool_ref='r4')]))
        jsonschema.validate(patch,schema);before=copy.deepcopy(parent);result=apply(parent,patch,packet)
        self.assertEqual(parent,before);self.assertEqual(result['items'],before['items'])
        self.assertEqual(result['summary'],before['summary']);self.assertEqual(result['task_summaries']['t']['goal'],'原目标')
        self.assertEqual(result['task_summaries']['t']['workflow'],patch['task_workflows']['t'])
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(dict(patch,items=dict(P1=dict(band='exceptional'))),schema)
        with self.assertRaises(ValueError):apply(parent,dict(patch,task_workflows=dict(other=[])),packet)

    def test_repair_restores_real_adjacent_feedback_and_preserves_judgment(self):
        t=task();source={row['id']:dict(row,task_id=t['id']) for row in t['records']}
        people=[row for row in t['records'] if row['role']=='user']
        background=next(row for row in t['records'] if row['role']=='assistant')
        data,schema,transport=encode([t],spans([dict(t,records=people)]),source,[],[],[background['id']])
        fragment=next(key for key,(ref,_) in transport['fragments'].items() if ref==people[0]['id'])
        cell=dict(band='good',reason='只有安排，尚未看到执行。',next_band='',
            evidence=[dict(fragment_id=fragment,claim='本人安排',origin='personal')],context_refs=[],negative=None)
        correction=dict(previous_invalid=dict(items=dict(P1=cell)),validation_errors='P1: 依据缺少相邻执行反馈')
        before=copy.deepcopy(correction)
        expanded,contract,new_transport,records,added=with_context(data,schema,[t],source,correction)
        self.assertIn(background['id'],added);self.assertEqual(correction,before)
        self.assertEqual(new_transport['fragments'],transport['fragments'])
        restored=next(row for row in records if row['id']==background['id'])
        self.assertEqual(restored['text'],background['text'])
        self.assertNotIn(new_transport['aliases'][background['id']],expanded['available_read_ids'])
        again=with_context(expanded,contract,[t],source,correction)
        self.assertEqual(again[-1],[])

    def test_workflow_directory_explicitly_binds_name_to_actual_call(self):
        t=task();t['workflow_index']=[dict(name='synthetic-mcp',record_id=t['records'][-1]['id'],chars=10)]
        data,_,_=encode([t],spans([t]),{row['id']:row for row in t['records']},[],[],[])
        self.assertEqual(data['tasks'][0]['workflow_index'][0]['record_id'],'r'+str(len(t['records'])))
        self.assertEqual(data['tasks'][0]['workflow_index'][0]['name'],'synthetic-mcp')

    def test_provenance_rebuilds_context_from_frozen_source(self):
        from conversation_provenance import candidate
        t=task();originals={row['id']:row for row in t['records']}
        people=[row for row in t['records'] if row['role']=='user']
        data,schema,transport=encode([t],spans([dict(t,records=people)]),originals,[],[],['a'])
        ref=next(iter(transport['fragments']))
        cell=dict(band='good',reason='原判断',next_band='',evidence=[dict(fragment_id=ref,claim='本人选择',origin='personal')],context_refs=[],negative=None)
        parent=dict(summary='理解',task_summaries={},items=dict(P1=cell),requests=[],queries=[])
        correction=dict(previous_invalid=parent,validation_errors='P1: 需要相邻语境')
        expanded,contract,_,_,refs=with_context(data,schema,[t],originals,correction)
        packet,patch_contract=package(expanded,contract,correction)
        change=dict(items=dict(P1=dict(cell,reason='已核相邻反馈')),summary=None)
        merged=apply(parent,change,packet)
        def stages(root,name):
            return ((parent,dict(data=data,schema=schema),'parent') if name=='synthesis-v-0'
                else (change,dict(data=packet,schema=patch_contract),'patch'))
        with TemporaryDirectory() as d:
            root=Path(d);name='repair-v-1'
            (root/(name+'.assembly.json')).write_text(json.dumps(dict(parent_stage='synthesis-v-0',context_refs=refs,
                original_data_sha256=digest(expanded),result_sha256=digest(merged))))
            (root/(name+'.assembled.json')).write_text(json.dumps(merged))
            with patch('conversation_provenance.stage',side_effect=stages):
                self.assertEqual(candidate(root,name,source=([t],originals))[0],merged)
                with self.assertRaisesRegex(ValueError,'绑定冻结原文'):candidate(root,name)
                altered=copy.deepcopy(originals);altered['a']['text']='伪造的执行成功'
                with self.assertRaisesRegex(ValueError,'材料与原综合判断'):candidate(root,name,source=([t],altered))

    def fixture(self):
        t=task();data,schema,transport=encode([t],spans([t]),{r['id']:r for r in t['records']},[],[],[])
        fragments=list(transport['fragments'])
        def cell(ref):return dict(band='good',reason='原判断',next_band='',
            evidence=[dict(fragment_id=ref,claim='本人行为',origin='personal')],context_refs=[],negative=None)
        value=dict(summary='综合理解',task_summaries={},items=dict(P1=cell(fragments[0]),J1=cell(fragments[-1])),requests=[],queries=[])
        correction=dict(previous_invalid=value,quality_findings=[dict(item='P1',claim_pointer='/items/P1/reason',criterion='P1.good',problem='具体事实需要修正',source_refs=[])])
        packet,contract=package(data,schema,correction)
        fixed=copy.deepcopy(value['items']['P1']);fixed['reason']='仅纠正事实确定性'
        return data,schema,value,packet,contract,dict(items=dict(P1=fixed),summary=None)

    def test_only_targeted_item_changes_and_parent_is_preserved(self):
        _,_,parent,packet,schema,change=self.fixture();before=copy.deepcopy(parent)
        jsonschema.validate(change,schema);merged=apply(parent,change,packet)
        self.assertEqual(parent,before)
        self.assertEqual(merged['items']['J1'],parent['items']['J1'])
        self.assertEqual(merged['summary'],parent['summary'])
        self.assertEqual(merged['task_summaries'],parent['task_summaries'])
        self.assertEqual(merged['items']['P1']['reason'],'仅纠正事实确定性')

    def test_extra_items_unread_evidence_and_unrequested_summary_are_rejected(self):
        _,_,parent,packet,schema,change=self.fixture()
        extra=copy.deepcopy(change);extra['items']['J1']=parent['items']['J1']
        with self.assertRaises(jsonschema.ValidationError):jsonschema.validate(extra,schema)
        with self.assertRaisesRegex(ValueError,'范围不一致'):apply(parent,extra,packet)
        unread=copy.deepcopy(change);unread['items']['P1']['evidence'][0]['fragment_id']='q999'
        with self.assertRaisesRegex(ValueError,'未交付'):apply(parent,unread,packet)
        with self.assertRaisesRegex(ValueError,'整体解读'):apply(parent,dict(change,summary='擅自改写'),packet)

    def test_missing_item_can_be_added_with_full_anchors_and_received_evidence(self):
        data,schema,parent,_,_,change=self.fixture()
        finding=dict(item='M3',claim_pointer='/summary',criterion='M3.reasonable',problem='遗漏已有独立调整',source_refs=['r3'])
        packet,contract=package(data,schema,dict(previous_invalid=parent,quality_findings=[finding]))
        self.assertEqual(packet['rubric']['performance_anchors']['M3'],data['rubric']['performance_anchors']['M3'])
        patch=dict(items=dict(M3=change['items']['P1']),summary=None)
        jsonschema.validate(patch,contract)
        merged=apply(parent,patch,packet)
        self.assertIn('M3',merged['items']);self.assertEqual(merged['items']['P1'],parent['items']['P1'])

    def test_provenance_rebuilds_patch_and_rejects_tampered_untouched_item(self):
        from conversation_provenance import candidate
        data,schema,parent,packet,contract,change=self.fixture();merged=apply(parent,change,packet)
        def stages(root,name):
            return ((parent,dict(data=data,schema=schema),'parent-proof') if name=='synthesis-v-0'
                else (change,dict(data=packet,schema=contract),'patch-proof'))
        with TemporaryDirectory() as d:
            root=Path(d);name='repair-v-1'
            (root/(name+'.assembly.json')).write_text(json.dumps(dict(parent_stage='synthesis-v-0',
                original_data_sha256=digest(data),result_sha256=digest(merged))))
            (root/(name+'.assembled.json')).write_text(json.dumps(merged))
            with patch('conversation_provenance.stage',side_effect=stages):
                value,_,proofs=candidate(root,name)
                self.assertEqual(value,merged);self.assertEqual(proofs,['parent-proof','patch-proof'])
                altered=copy.deepcopy(merged);altered['items']['J1']['band']='outstanding'
                (root/(name+'.assembled.json')).write_text(json.dumps(altered))
                with self.assertRaisesRegex(ValueError,'合并结果被改写'):candidate(root,name)


if __name__=='__main__':unittest.main()
