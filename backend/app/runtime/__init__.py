"""
Agent Runtime 包：负责把上传的 agent 代码包跑起来。

子模块：
- storage：COS 上传/下载
- venv：构建/复用 venv
- process：子进程管理 helper
- manager：单 agent 生命周期
- registry：所有 agent 的注册中心
"""
