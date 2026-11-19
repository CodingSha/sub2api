"""说明schema及代表案例选择的无损合同；全合成，不证明语义或生产收益。"""
import copy
import json
import random
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from review_schema import compact
from runtime import digest
import task_interpretation as role
import week_basis as week
from test_week_basis_case_scope import sample
try:
    from jsonschema import Draft202012Validator
except ImportError:
    Draft202012Validator = None

# 冻结自优化前源码；不依赖本机.dev目录，VERSION由当前模块统一注入后比较内容。
OLD_TRANSPORT = '''
def transport(unit, report):
    # 代表案例先按原顺序查找保存了真实来回记录的任务；不读取分数或结果选择。
    available = []
    for source in unit['source_units']:
        task, records, limits, status = records_for(unit, source['key'])
        if not records or len(records) > 1000 or size(records) > MAX_RECORDS:
            continue
        value = dict(version=VERSION, task_key=source['key'], title=clean(task['title']),
            goal=clean(task.get('purpose') or task['title']), records=records,
            context_status=status, limitations=limits, rubric=copy.deepcopy(RUBRIC),
            week_items=clean([{k: i[k] for k in ('item_code', 'state', 'reason')} for i in report['items']]),
            selection_note='按原任务顺序优先取含已确认本人输入和非空AI回复的有界案例；原稀疏案例及其异议保留，其他任务未重新核查。',
            scope_note='仅本任务可展示记录；原周判断不是新的来源证据，不能声明全部任务已核。')
        if size(value) <= MAX_INPUT - 18000:
            available.append(value)
    for value in available:
        if any(r['role'] == 'assistant' and r['text'].strip() for r in value['records']):
            return value
    if available:
        return available[0]
    raise Paused('needs_review', '没有在容量内可完整提供的任务案例；原评分保留，不截断或凑案例')
'''
OLD_BUILD = '''
def build(unit, report):
    formal = {k for ob in report['eligibility']['observations'] for k in ob['task_keys']}
    if not formal:
        raise Paused('needs_review', '仅局部表达，现有正式任务附加合同不能承载，不冒充任务')
    if formal != {s['key'] for s in unit['source_units'] if s['source_kind'] == 'task'}:
        raise ValueError('正式任务资格与冻结来源集合不一致')
    sources, records = [], {}
    for source in unit['source_units']:
        task, rows, limits, status = role.records_for(unit, source['key'])
        if not rows:
            raise Paused('needs_review', '来源没有可核记录，不宣称整周已核')
        for row in rows:
            if row['id'] in records and records[row['id']] != row:
                raise ValueError('同一记录出现不同内容')
            records[row['id']] = row
        sources.append(dict(key=task['key'], source_kind=task['source_kind'], title=role.clean(task['title']),
            goal=role.clean(task.get('purpose') or task['title']), record_ids=[r['id'] for r in rows],
            context_status=status, limitations=limits))
    case_unit = copy.copy(unit)
    case_unit['source_units'] = [s for s in unit['source_units'] if s['key'] in formal]
    selected = role.transport(case_unit, report)
    result = dict(version=VERSION, scope=report['workday_scope'], rubric=copy.deepcopy(RUBRIC),
        source_units=sources, formal_task_keys=[k for k in unit['source_keys'] if k in formal],
        records=list(records.values()), case=selected,
        week_items=role.clean([{k: i[k] for k in ('item_code','state','score','reason','criterion')} for i in report['items']]))
    # 原文只传一份，选中案例由明确ID索引同一记录集。
    result['case_record_ids'] = [r['id'] for r in selected.pop('records')]
    selected.pop('rubric')
    selected.pop('week_items')
    return result
'''


def expand(schema):
    definitions=schema.get('$defs',{})
    def walk(value,seen=()):
        if isinstance(value,list):return [walk(v,seen) for v in value]
        if not isinstance(value,dict):return value
        if '$ref' in value:
            if set(value)!={'$ref'}:raise ValueError('引用旁含未核约束')
            ref=value['$ref']
            if not ref.startswith('#/$defs/'):raise ValueError('不允许外部引用')
            name=ref[len('#/$defs/'):]
            if name in seen:raise ValueError('引用循环')
            return walk(definitions[name],seen+(name,))
        return {k:walk(v,seen) for k,v in value.items() if k!='$defs'}
    return walk(schema)


def legacy_functions():
    # 历史算法保留当时估值，不随当前精确wire准入改变其测试基线。
    namespace=dict(role.__dict__,MAX_RECORDS=80000,MAX_INPUT=120000);exec(OLD_TRANSPORT,namespace)
    legacy_role=SimpleNamespace(**role.__dict__);legacy_role.transport=namespace['transport']
    week_namespace=dict(week.__dict__);week_namespace['role']=legacy_role
    exec(OLD_BUILD,week_namespace)
    return namespace['transport'],week_namespace['build'],legacy_role


def candidates(kinds):
    result={}
    for i,kind in enumerate(kinds):
        text='合成'*50000 if kind=='huge' else '' if kind=='blank_ai' else '合成'
        records=[] if kind=='empty' else [dict(id='r'+str(i),role='assistant' if kind in ('ai','blank_ai') else 'user',text=text)]
        result[str(i)]=(dict(key=str(i),title='合成',source_kind='task'),records,[], 'available_records')
    return result


class CompactionTests(unittest.TestCase):
    def schemas(self,prior=False):
        data,spec,audit=sample(week)
        task_data=dict(task_key=spec['case']['task_key'],records=[r for r in data['records'] if r['id'] in data['case_record_ids']])
        task_audit=dict(seen_record_ids=data['case_record_ids'],claims=copy.deepcopy(audit['case_claims']),checks=copy.deepcopy(audit['case_checks']),issues=[])
        if prior:
            data['prior_feedback']=dict(points={'p0':{},'p1':{}})
            audit['prior_points']={k:dict(verdict='resolved',reason='合成',record_ids=[data['case_record_ids'][0]]) for k in ('p0','p1')}
            task_data['revision_feedback']=dict(review_points={'p0':{},'p1':{}})
            task_audit['prior_points']={k:dict(verdict='resolved',reason='合成',record_ids=[data['case_record_ids'][0]],claim_keys=['claim_0']) for k in ('p0','p1')}
        return data,spec,[(week.schema(data),spec),(week.audit_schema(data,spec),audit),
            (role.generation_schema(task_data),spec['case']),(role.audit_schema(task_data,spec['case']),task_audit)]

    def test_four_schemas_expand_to_complete_original_and_preserve_sources(self):
        for prior in (False,True):
            data,spec,groups=self.schemas(prior);before=copy.deepcopy((data,spec,groups))
            for schema,_ in groups:
                with self.subTest(prior=prior):self.assertEqual(schema,expand(compact(schema)))
            self.assertEqual(before,(data,spec,groups))

    def test_actual_payload_uses_exact_compaction_without_mutating_input(self):
        data,spec,groups=self.schemas();schema,_=groups[1];before=copy.deepcopy((data,schema))
        client=SimpleNamespace(config=SimpleNamespace(model='synthetic'))
        request=role.payload(client,'synthetic','合成',data,schema)
        self.assertEqual(compact(schema),request['text']['format']['schema'])
        self.assertEqual(before,(data,schema));self.assertEqual(schema,expand(request['text']['format']['schema']))

    def test_compacted_audit_minimum_is_a_lower_bound_for_three_to_six_claims(self):
        client=SimpleNamespace(config=SimpleNamespace(model='synthetic'))
        for prior in (False,True):
            data,spec,_=self.schemas(prior);before=copy.deepcopy((data,spec))
            cost=week.capacity(client,data)
            for count in (3,4,5,6):
                actual=copy.deepcopy(spec)
                actual['case']['claims']=(actual['case']['claims']*2)[:count]
                request=week.request(client,'zhiyongli_week_basis_audit',week.AUDIT,
                    dict(source=data,interpretation=actual),week.audit_schema(data,actual))
                self.assertLessEqual(cost['audit_body_lower_bound_bytes'],role.request_size(request))
            self.assertEqual(before,(data,spec))

    @unittest.skipUnless(Draft202012Validator,'jsonschema未安装；完整专项须使用已登记测试环境，不自动联网安装')
    def test_jsonschema_positive_and_negative_constraints_remain_identical(self):
        for prior in (False,True):
            data,spec,groups=self.schemas(prior)
            for index,(schema,positive) in enumerate(groups):
                small=compact(schema);Draft202012Validator.check_schema(small)
                validators=[Draft202012Validator(schema),Draft202012Validator(small)]
                variants=[(copy.deepcopy(positive),True)]
                def invalid(change):
                    value=copy.deepcopy(positive);change(value);variants.append((value,False))
                invalid(lambda v:v.update(extra='forbidden'))
                invalid(lambda v:v.pop(next(iter(v))))
                if index==0:
                    invalid(lambda v:v['items']['P1'].update(record_ids=['foreign']))
                    invalid(lambda v:v['items']['P1'].update(limitations=['x']*13))
                    invalid(lambda v:v['case'].update(claims=v['case']['claims'][:2]))
                    invalid(lambda v:v['case'].update(claims=v['case']['claims']*3))
                elif index==1:
                    invalid(lambda v:v['case_checks']['goal'].update(record_ids=['other-task-record']))
                    invalid(lambda v:v.update(seen_record_ids=[]))
                    if prior:invalid(lambda v:v['prior_points'].pop('p0'))
                elif index==2:
                    invalid(lambda v:v['claims'][0].update(item_codes=['X1']))
                    invalid(lambda v:v['claims'][0].update(record_ids=[]))
                    invalid(lambda v:v.update(limitations=[]))
                else:
                    invalid(lambda v:v['claims'].update(claim_99=v['claims'].pop('claim_0')))
                    invalid(lambda v:v['checks']['goal'].update(record_ids=['other-task-record']))
                    if prior:invalid(lambda v:v['prior_points']['p0'].update(claim_keys=['claim_99']))
                for value,expected in variants:
                    with self.subTest(schema=index,prior=prior,expected=expected):
                        self.assertEqual([expected,expected],[v.is_valid(value) for v in validators])


class SelectionTests(unittest.TestCase):
    def compare_transport(self,kinds):
        old,_,_=legacy_functions();dataset=candidates(kinds)
        unit=dict(source_units=[dict(key=k) for k in dataset]);report=dict(items=[dict(item_code='P1',state='observed',reason='合成')])
        before=copy.deepcopy((unit,report,dataset));outcomes=[]
        for func in (old,role.transport):
            calls=[]
            def records_for(unit,key):
                calls.append(key)
                if kinds[int(key)]=='error':raise ValueError('合成后续来源异常')
                return copy.deepcopy(dataset[key])
            with patch.dict(func.__globals__,records_for=records_for):
                try:result=dict(value=func(unit,report))
                except Exception as error:result=dict(error=type(error).__name__,message=str(error))
            outcomes.append((result,calls))
        self.assertEqual(outcomes[0],outcomes[1]);self.assertEqual(before,(unit,report,dataset))
        return outcomes[1]

    def test_old_new_selection_errors_and_visit_order_match(self):
        rng=random.Random(1116)
        for i in range(80):
            kinds=rng.choices(('user','ai','blank_ai','empty','huge','error'),k=rng.randrange(0,12))
            with self.subTest(case=i):self.compare_transport(kinds)

    def test_late_error_is_not_hidden_after_a_preferred_case(self):
        result,calls=self.compare_transport(['ai','user','error'])
        self.assertEqual('ValueError',result['error']);self.assertEqual(['0','1','2'],calls)

    def test_empty_assistant_falls_back_but_later_real_assistant_has_priority(self):
        result,_=self.compare_transport(['user','blank_ai']);self.assertEqual('0',result['value']['task_key'])
        result,calls=self.compare_transport(['user','blank_ai','ai','ai'])
        self.assertEqual('2',result['value']['task_key']);self.assertEqual(['0','1','2','3'],calls)

    def test_week_build_extracts_each_source_once_and_preserves_full_legacy_output(self):
        _,old_build,legacy_role=legacy_functions();dataset=candidates(['user','ai','user'])
        sources=[dict(key=k,source_kind='local_behavior' if k=='2' else 'task') for k in dataset]
        unit=dict(source_units=sources,source_keys=list(dataset),events=[])
        report=dict(eligibility=dict(observations=[dict(task_keys=['0','1'])]),workday_scope={'synthetic':True},
            items=[dict(item_code=k,state='observed',score=70,reason='合成',criterion='合成') for k in week.ITEMS])
        dataset['2'][0]['source_kind']='local_behavior';before=copy.deepcopy((unit,report,dataset));results=[];orders=[]
        for func,helper in ((old_build,legacy_role),(week.build,role)):
            calls=[]
            def records_for(unit,key,**kwargs):calls.append(key);return copy.deepcopy(dataset[key])
            with patch.object(helper,'records_for',side_effect=records_for):
                if helper is legacy_role:
                    with patch.dict(helper.transport.__globals__,records_for=records_for):result=func(unit,report)
                else:result=func(unit,report)
            results.append(result);orders.append(calls)
        self.assertEqual(results[0],results[1]);self.assertEqual(['0','1','2','0','1'],orders[0])
        self.assertEqual(['0','1','2'],orders[1]);self.assertEqual(before,(unit,report,dataset))


if __name__=='__main__':unittest.main()
