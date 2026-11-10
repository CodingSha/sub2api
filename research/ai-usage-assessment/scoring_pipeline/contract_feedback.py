"""合同诊断反馈v2：由调用方提供实际接纳校验器，诊断失败不覆盖原错误。"""
from .contract_diagnostics import known_issues

VERSION = 'contract-diagnostics-20261103.2'


def check_with_diagnostics(unit, value, validator):
    """成功返回实际校验原结果；错误汇总只帮助原有有限返修。"""
    try:
        return validator(unit, value)
    except (ValueError, KeyError, TypeError, AttributeError) as original:
        try:
            issues = known_issues(unit, value)
            locations = '；'.join(i['code'] + '@' + i['path'] for i in issues)
        except Exception:
            # 恶形数据也必须保留真正的合同错误，不让辅助诊断遮蔽它。
            locations = ''
        raise ValueError(str(original) + ('\n同份输出已检出：' + locations if locations else '')) from original
