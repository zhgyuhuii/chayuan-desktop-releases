"""本地模型索引（单机版保留 local_index 用于 ai_platform / runtime 跟踪本机模型权重）。

「全网模型目录」相关模块（catalog / crawler / marketplace / model_packs /
seed / watcher / identifier）已在单机版重构中移除——单机用户只关心已配置的
厂商和模型，不需要爬全网模型搜索引擎。
"""
