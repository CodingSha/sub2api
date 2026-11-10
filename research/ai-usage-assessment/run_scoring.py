"""当前默认离线周汇总；历史流程必须显式选择，不调用评分API。"""
import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == 'month':
        sys.argv.pop(1)
        from scoring_pipeline.weekly_performance.monthly_cli import main as run
    elif len(sys.argv) > 1 and sys.argv[1] == 'legacy':
        sys.argv.pop(1)
        from scoring_pipeline.runner import main as run
    elif len(sys.argv) > 1 and sys.argv[1].startswith('behavior-v'):
        from scoring_pipeline.behavior_cli import main as behavior_main
        from scoring_pipeline import progressive_review, applicable_review, staged_review, stratified_review
        versions = {'behavior-v11': progressive_review, 'behavior-v10': applicable_review,
                    'behavior-v9': staged_review, 'behavior-v8': stratified_review}
        version = sys.argv.pop(1)
        if version == 'behavior-v7':
            return behavior_main()
        if version not in versions:
            raise SystemExit('未登记的历史流程版本')
        return behavior_main(versions[version])
    else:
        if len(sys.argv) > 1 and sys.argv[1] == 'week':
            sys.argv.pop(1)
        from scoring_pipeline.weekly_performance.__main__ import main as run
    return run()


if __name__ == '__main__':
    main()
