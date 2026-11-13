"""平台解密插件。

每个模块实现 ``base.Decoder`` 协议并在导入时 ``register()`` 自己。
新增平台 = 新增一个文件，不用改上层任何代码。
"""

from . import base  # noqa: F401  触发内置平台注册

__all__ = ["base"]
