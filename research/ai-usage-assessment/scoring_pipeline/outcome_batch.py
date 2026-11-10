"""独立成果观察入口：复用持久批处理，使用隔离的合同、运行目录和回执。"""
from . import outcome_review, stable_batch

if __name__ == '__main__':
    stable_batch.main(contract=outcome_review)
