"""v7行为评审的确定性入口；不调用模型、不提取数据、不兼容混用旧评分。"""
import argparse
import json
from collections import defaultdict
from pathlib import Path

from .calibrated_review import VERSION,validate_period,summarize_period
from .common import require


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def main(engine=None):
    version=engine.VERSION if engine else VERSION
    validate_fn=engine.validate_period if engine else validate_period
    summarize_fn=engine.summarize_period if engine else summarize_period
    parser=argparse.ArgumentParser(description=version+' 本人行为评审校验与精确汇总')
    commands=parser.add_subparsers(dest='command',required=True)
    validate=commands.add_parser('validate',help='按原始本人片段、期间和固定标准校验完整评审')
    validate.add_argument('--input',required=True)
    validate.add_argument('--review',required=True)
    validate.add_argument('--output',required=True)
    summarize=commands.add_parser('summarize',help='按人和期间汇总已校验观察；不平均周分')
    summarize.add_argument('--reviews',required=True)
    summarize.add_argument('--output',required=True)
    if engine and hasattr(engine,'apply_monthly'):
        monthly=commands.add_parser('month',help='用整月表现与完整周使用事实计算月度等级')
        monthly.add_argument('--summary',required=True)
        monthly.add_argument('--period',required=True)
        monthly.add_argument('--windows',required=True)
        monthly.add_argument('--output',required=True)
    args=parser.parse_args()
    if args.command=='validate':
        data=read(args.input);review=read(args.review)
        periods={p['period_id']:p for p in data['periods']}
        require({p['period_id'] for p in review['periods']}==set(periods)
                and len(review['periods'])==len(periods),'评审期间须完整覆盖输入')
        result={'policy_version':version,'periods':[validate_fn(data,periods[p['period_id']],p) for p in review['periods']]}
    elif args.command=='month':
        summary=read(args.summary)
        require(summary.get('policy_version')==version,'月度汇总拒绝其他评分版本')
        result=dict(summary,**engine.apply_monthly(summary['exact'],read(args.period),read(args.windows)))
    else:
        rows=read(args.reviews)
        require(isinstance(rows,list) and rows,'须提供已校验的期间观察列表')
        groups=defaultdict(list);identities=set()
        for row in rows:
            require(row.get('policy_version')==version,'拒绝混用其他版本')
            key=(row['user_id'],row['period_id'],row['goal_id'])
            require(key not in identities,'同一家族期间重复，不能重复加权')
            identities.add(key);groups[key[:2]].append(row)
        result={'policy_version':version,'rows':[dict(user_id=u,period_id=p,**summarize_fn(values)) for (u,p),values in sorted(groups.items())],
                'scope':'仅汇总已校验行为表现；未知不补分。月度参与度须另按同范围已核验使用事实计算，不从周分推导。'}
    path=Path(args.output)
    require(not path.exists(),'输出已存在，须选择新路径，避免覆盖已冻结结果')
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'policy_version':version,'output':str(path)},ensure_ascii=False))
