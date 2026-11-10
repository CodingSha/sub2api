"""版本化法定调休日历与公司覆盖；仅生成范围计划，不入队、不评分。"""
import argparse
from datetime import date,datetime,time,timedelta,timezone
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

TZ=timezone(timedelta(hours=8))
VERSION='workday-plan-20261109.1'


class Calendar:
    def __init__(self,documents,company=None):
        self.years={};self.overrides={}
        for value in documents:
            year=value.get('year');source=value.get('source',{})
            if (type(year) is not int or year in self.years or not value.get('version')
                or value.get('timezone')!='Asia/Shanghai'
                or urlsplit(source.get('url','')).hostname!='www.gov.cn'
                or not source.get('document') or not re.fullmatch('[a-f0-9]{64}',source.get('body_sha256',''))):
                raise ValueError('法定日历缺年份、版本或已核官方来源签名')
            rest=set()
            for a,b in value['rest_ranges']:
                lo,hi=date.fromisoformat(a),date.fromisoformat(b)
                if lo.year!=year or hi.year!=year or hi<lo:raise ValueError('假期范围越年或倒置')
                dates={lo+timedelta(days=n) for n in range((hi-lo).days+1)}
                if rest & dates:raise ValueError('假期范围重复')
                rest.update(dates)
            makeup=[date.fromisoformat(d) for d in value['makeup_workdays']]
            if len(set(makeup))!=len(makeup) or any(d.year!=year or d in rest for d in makeup):
                raise ValueError('补班日期重复、越年或与放假冲突')
            self.years[year]=(rest,set(makeup))
        if company is not None:
            if not company.get('version') or not company.get('approved_by') or not company.get('reason'):
                raise ValueError('公司覆盖缺版本、确认人或依据')
            for item in company['dates']:
                d=date.fromisoformat(item['date'])
                if d in self.overrides or type(item.get('working')) is not bool or not item.get('reason'):
                    raise ValueError('公司覆盖日期重复或缺明确状态和理由')
                if d.year not in self.years:raise ValueError('公司覆盖不能代替缺失年份的法定日历')
                self.overrides[d]=item['working']
        raw=json.dumps(dict(official=documents,company=company),sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()
        self.signature=hashlib.sha256(raw).hexdigest()

    def working(self,day):
        if day.year not in self.years:raise ValueError('未配置该年份法定日历，禁止猜测工作日')
        if day in self.overrides:return self.overrides[day]
        rest,makeup=self.years[day.year]
        return day in makeup or day.weekday()<5 and day not in rest

    def week_plan(self,start):
        a=date.fromisoformat(start)
        if a.weekday()!=0:raise ValueError('周周期键必须为周一')
        days=[a+timedelta(days=n) for n in range(7)]
        workdays=[d for d in days if self.working(d)]
        value=dict(version=VERSION,calendar_sha256=self.signature,period_start=str(a),period_end=str(a+timedelta(days=7)),
            working_dates=list(map(str,workdays)),context_dates=[str(d) for d in days if d not in workdays],
            execution='plan_only_not_queued',publication='admin_confirmation')
        if not workdays:return dict(value,state='non_evaluation_week',cutoff=None,freeze_at=None,candidate_due_at=None)
        following=a+timedelta(days=7)
        while not self.working(following):following+=timedelta(days=1)
        cutoff=datetime.combine(workdays[-1]+timedelta(days=1),time(),TZ)
        freeze=cutoff+timedelta(hours=2);due=datetime.combine(following,time(8),TZ)
        return dict(value,state='planned',cutoff=cutoff.isoformat(),freeze_at=freeze.isoformat(),candidate_due_at=due.isoformat(),
            processing_hours=(due-freeze).total_seconds()/3600)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--calendar',type=Path,action='append',required=True)
    parser.add_argument('--company-overrides',type=Path)
    parser.add_argument('--week',required=True)
    args=parser.parse_args()
    company=json.loads(args.company_overrides.read_text()) if args.company_overrides else None
    value=Calendar([json.loads(p.read_text()) for p in args.calendar],company)
    print(json.dumps(value.week_plan(args.week),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
