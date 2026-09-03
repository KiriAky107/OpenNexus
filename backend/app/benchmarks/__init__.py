"""Benchmark 服务：RAG / Agent 数据集注册、指标计算与运行管理。

模块划分：
- metrics.py  纯函数指标（Hit@K / Recall@K / MRR / CitationHit / 分位数）
- datasets.py 受控目录的 Dataset 注册与校验
- rag.py      RAG Benchmark Runner（调用 retrieval.engine.search）
- service.py  运行注册表、配置快照与报告组装
"""
