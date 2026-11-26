"""验证冻结来源只包含入选区间及其工具，防止跨任务借证。"""
import copy
import unittest

from conversation_source import bind
from daily_tasks import materialize,turns


class ConversationSourceTests(unittest.TestCase):
    def fixture(self):
        rows=[dict(id=ref,role=role,text=text,truncated=False) for ref,role,text in [
            ('u1','user','请核查甲任务。'),('tool1','tool','甲工具结果'),('a1','assistant','甲结果'),
            ('u2','user','请核查乙任务。'),('tool2','tool','乙工具结果'),('a2','assistant','乙结果')]]
        groups=[dict(session='s',records=rows)]
        tasks,_=materialize(dict(tasks=[dict(start='u1',end='u1',title='甲',goal='核查甲')]),turns(groups))
        return tasks,{r['id']:r for r in rows},dict(selected=[dict(session='s',record_ids=[r['id'] for r in rows])])

    def test_whole_day_index_is_restricted_to_selected_task_and_tools(self):
        source=bind(*self.fixture())
        self.assertEqual(set(source['originals']),{'u1','tool1','a1'})
        self.assertTrue(all(r['task_id']=='task-1' for r in source['originals'].values()))

    def test_changed_spine_or_session_is_rejected(self):
        for kind in ('text','session'):
            tasks,originals,selection=self.fixture();tasks=copy.deepcopy(tasks)
            if kind=='text':tasks[0]['records'][0]['text']='另一条指令'
            else:tasks[0]['session']='other'
            with self.subTest(kind=kind),self.assertRaises(ValueError):bind(tasks,originals,selection)


if __name__=='__main__':unittest.main()
