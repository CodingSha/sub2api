"""个人评分范围；独立于审计采集白名单，不删除来源或历史回执。"""
import os


def excluded_user_ids():
    raw = os.environ.get('ZHIYONGLI_EXCLUDED_USER_IDS', '')
    if raw == '':
        return []
    result = []
    for part in raw.split(','):
        part = part.strip()
        if not part or not all('0' <= c <= '9' for c in part):
            raise ValueError('个人评分排除配置须为逗号分隔的正整数账户ID')
        value = int(part)
        if not 0 < value <= 2**63-1 or value in result:
            raise ValueError('个人评分排除配置有无效或重复账户ID')
        result.append(value)
    return result


def require_personal_user(user_id):
    if user_id in excluded_user_ids():
        raise ValueError('该账户已排除个人评分；原始记录与历史结果保留')
