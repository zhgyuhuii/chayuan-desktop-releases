"""95 题:文件夹定时同步系统。

模块清单:
* :mod:`scanner`   — 扫目录 + 对比 state 文件,产 diff
* :mod:`uploader`  — 把 diff 按 mime 分发到 doc_kb / image_src
* :mod:`scheduler` — apscheduler 包装,按 job.interval 触发(95-4 接入)
"""
