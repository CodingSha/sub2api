"""由单一日历计划生成跨语言范围合同；周/月周期键保留，非工作日只作背景。"""
import argparse
from datetime import date,datetime,time,timedelta
import hashlib
import json
import os
from pathlib import Path

from work_calendar import Calendar,TZ

VERSION='workday-scope-20261109.1'
REGISTRY_VERSION='workday-registry-20261109.1'
ROOT=Path(__file__).resolve().parents[2]
DEFAULT=ROOT/'backend/internal/zhiyongli/workday-registry.json'


def registry(calendar):
    weeks={};unsupported=[]
    for year in sorted(calendar.years):
        cursor=date(year,1,1)
        cursor+=timedelta(days=(7-cursor.weekday())%7)
        while cursor.year==year:
            try:weeks[str(cursor)]=calendar.week_plan(str(cursor))
            except ValueError:unsupported.append(str(cursor))
            cursor+=timedelta(days=7)
    return dict(version=REGISTRY_VERSION,calendar_sha256=calendar.signature,weeks=weeks,unsupported_weeks=unsupported)


def load(path=None,expected=None):
    path=Path(path or os.environ.get('ZHIYONGLI_WORKDAY_REGISTRY') or DEFAULT)
    raw=path.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    if expected and expected!=sha:
        # 只允许已登记的受信归档，不按请求指定任意本地路径。
        if len(expected)!=64 or any(c not in '0123456789abcdef' for c in expected):raise ValueError('日历范围签名无效')
        if path!=DEFAULT:
            default=DEFAULT.read_bytes()
            if hashlib.sha256(default).hexdigest()==expected:return json.loads(default),expected
        archive=os.environ.get('ZHIYONGLI_WORKDAY_REGISTRY_ARCHIVE')
        if not archive:raise ValueError('冻结日历范围未在受信登记或归档中')
        raw=(Path(archive)/(expected+'.json')).read_bytes();sha=hashlib.sha256(raw).hexdigest()
        if sha!=expected:raise ValueError('归档日历范围签名损坏')
    value=json.loads(raw)
    if value.get('version')!=REGISTRY_VERSION:raise ValueError('日历范围登记版本未识别')
    return value,sha


def window(start,end,registered=None):
    value,sha=registered or load()
    a,b=date.fromisoformat(start),date.fromisoformat(end)
    if not a<b:raise ValueError('工作日范围起止无效')
    cursor=a-timedelta(days=a.weekday());working=[];freezes=[];dues=[]
    while cursor<b:
        plan=value['weeks'].get(str(cursor))
        if not plan:raise ValueError('评价范围缺少已核年份/跨年日历计划，禁止猜测')
        if plan.get('calendar_sha256')!=value['calendar_sha256']:raise ValueError('日历计划来源签名不一致')
        working.extend(d for d in plan['working_dates'] if a<=date.fromisoformat(d)<b)
        # 整周假日仍须封口留证，但不生成员工无活动周分。
        freezes.append(datetime.fromisoformat(plan['freeze_at']) if plan['freeze_at'] else
            datetime.combine(cursor+timedelta(days=7),time(2),TZ))
        if plan['candidate_due_at']:dues.append(datetime.fromisoformat(plan['candidate_due_at']))
        cursor+=timedelta(days=7)
    cutoff=datetime.combine(date.fromisoformat(working[-1])+timedelta(days=1),time(),TZ) if working else datetime.combine(b,time(),TZ)
    if a.day==1 and b==(a.replace(day=28)+timedelta(days=4)).replace(day=1):
        freezes.append(datetime.combine(b,time(2),TZ))
    return dict(version=VERSION,registry_sha256=sha,calendar_sha256=value['calendar_sha256'],
        period_start=start,period_end=end,working_dates=working,observed_until=cutoff.isoformat(),
        freeze_at=max(freezes).isoformat(),candidate_due_at=max(dues).isoformat() if dues else None)


def validate(scope):
    if scope!=window(scope['period_start'],scope['period_end'],load(expected=scope['registry_sha256'])):
        raise ValueError('工作日计分范围与已登记冻结日历不符')
    return scope


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calendar',type=Path,action='append',required=True)
    parser.add_argument('--company-overrides',type=Path);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    c=Calendar([json.loads(p.read_text()) for p in args.calendar],
        json.loads(args.company_overrides.read_text()) if args.company_overrides else None)
    args.output.write_text(json.dumps(registry(c),ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
