"""语义评审版本独立于023分值尺；历史判断不能靠资格转换升级。"""

REVIEW_POLICY = 'contextual-review-20261109.1'


def attach_review_policy(report, reviews):
    """只传播原评审显式声明的合同，不推定旧判断已按新规则复核。"""
    reviews = list(reviews)
    if reviews and all(r.get('review_policy') == REVIEW_POLICY for r in reviews):
        report['policy_versions']['review'] = REVIEW_POLICY
