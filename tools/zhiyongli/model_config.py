"""服务专用模型配置；不读取开发客户端的配置和凭证。"""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import stat
import urllib.request

MODEL = 'deepseek-v4.1-flash-local'


class ModelError(RuntimeError):
    """模型返回失败保留为执行状态，不转换为人员分数。"""


def response_text(response):
    """仅提取完整终态正文，不依赖旧实验客户端或开发凭证。"""
    if response.get('status') != 'completed':
        raise ModelError('response_not_completed')
    parts = []
    for item in response.get('output', []):
        for content in item.get('content', []):
            if content.get('type') == 'refusal':
                raise ModelError('model_refusal')
            if content.get('type') == 'output_text':
                parts.append(content['text'])
    if not parts:
        raise ModelError('empty_model_output')
    return ''.join(parts)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # 不把 Authorization 带到其他地址，也不在重定向后重复模型 POST。
        return None


@dataclass(frozen=True)
class ModelConfig:
    base_url: str
    key_file: Path
    timeout: int = 180
    model: str = MODEL

    @classmethod
    def load(cls, path=None):
        path = path or os.environ.get('ZHIYONGLI_MODEL_CONFIG')
        if not path:
            raise ValueError('未指定智用力专用模型配置文件')
        value = json.loads(Path(path).read_text())
        if set(value) - {'base_url', 'api_key_file', 'model', 'timeout_seconds'}:
            raise ValueError('模型配置含未登记字段')
        base = value['base_url']
        if not isinstance(base, str):
            raise ValueError('模型端点须为字符串')
        base = base.rstrip('/')
        if base not in {'https://ai.apsatcom.com', 'https://ai.apsatcom.com/v1',
                        'http://sub2api:8080', 'http://sub2api:8080/v1'}:
            raise ValueError('模型端点须为公司HTTPS接口或同网络http://sub2api:8080')
        if value.get('model') != MODEL:
            raise ValueError('本轮模型必须为deepseek-v4.1-flash-local')
        timeout = value.get('timeout_seconds', 180)
        if type(timeout) is not int or not 1 <= timeout <= 1800:
            raise ValueError('模型超时须为1至1800秒')
        key_file = Path(value['api_key_file'])
        if not key_file.is_absolute():
            raise ValueError('专用密钥文件须使用绝对路径')
        return cls(base, key_file, timeout)

    @property
    def responses_url(self):
        return self.base_url + ('/responses' if self.base_url.endswith('/v1') else '/v1/responses')

    def read_key(self):
        # 配置及日志只记录路径；专用文件不能允许组或其他用户读取。
        with self.key_file.open() as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or stat.S_IMODE(info.st_mode) not in {0o400, 0o600}:
                raise ValueError('专用密钥文件权限须为0400或0600')
            key = stream.read(8193).strip()
        if not key or len(key) > 8192 or any(c.isspace() for c in key):
            raise ValueError('专用密钥内容无效')
        return key

    def opener(self):
        # 固定公司入口直连，不继承桌面系统/环境代理，不把凭证及员工原文交给代理。
        # 不修改系统代理设置，也不在失败后切换入口或自动重发。
        return urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
