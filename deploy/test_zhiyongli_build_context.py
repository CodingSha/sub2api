"""用合成文件验证真实Docker上下文过滤；不读取员工正文、不拉取基础镜像。"""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


@unittest.skipUnless(shutil.which('docker'), '需要Docker构建器，基础镜像仅用scratch')
class BuildContextTests(unittest.TestCase):
    def test_private_review_data_excluded_and_release_sources_retained(self):
        with tempfile.TemporaryDirectory(prefix='zy-build-context-') as tmp:
            root=Path(tmp);context=root/'source';context.mkdir()
            ignore=Path(__file__).resolve().parents[1]/'.dockerignore'
            (context/'.dockerignore').write_bytes(ignore.read_bytes())
            excluded=['.dev/model.json','outputs/raw.json','.tmp_review/review.json',
                'plugins/private.key','.env','docs/ZHIYONGLI_AUTOMATION_DEPLOY.md',
                'docs/nested/private.json','docs/legal/private.key',
                'research/ai-usage-assessment/private/raw.json',
                'research/other/private.json']
            included=['backend/internal/synthetic.go','frontend/src/Synthetic.vue',
                'tools/zhiyongli/synthetic.py','research/ai-usage-assessment/scoring_pipeline/synthetic.py',
                'research/ai-usage-assessment/score-levels-g2.json',
                'research/ai-usage-assessment/scoring_pipeline/weekly_performance/contract-instructions.txt',
                'docs/legal/synthetic.md','docs/ZHIYONGLI_SCORING_RULES_V1.md',
                'docs/ZHIYONGLI_SCORING_RULES_V1_6.md']
            for name in excluded+included:
                path=context/name;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_text('synthetic-only:'+name)
            (context/'Dockerfile').write_text('FROM scratch\nCOPY . /context\n')
            destination=root/'export'
            result=subprocess.run(['docker','build','--network','none','--progress','plain',
                '--output','type=local,dest='+str(destination),str(context)],
                capture_output=True,text=True,timeout=60)
            self.assertEqual(0,result.returncode,
                '合成Docker构建上下文检查失败：\n'+result.stdout+result.stderr)
            for name in excluded:
                self.assertFalse((destination/'context'/name).exists(),name+'未排除')
            for name in included:
                self.assertEqual('synthetic-only:'+name,(destination/'context'/name).read_text())


if __name__=='__main__':unittest.main()
