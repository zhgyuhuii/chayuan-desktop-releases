"""RAPTOR 的聚类实现：UMAP（可选）降维 + GMM 软聚类。

单独成文件便于测试 & 替换算法（未来可换 HDBSCAN / K-Means）。
"""
from __future__ import annotations

import logging
from typing import List, Optional, Tuple

logger = logging.getLogger("chayuan.raptor.clustering")


def _reduce_dim(vectors, target_dim: int = 10) -> "np.ndarray":
    """优先 UMAP；未装 UMAP 时退化 PCA。"""
    import numpy as np
    X = np.asarray(vectors, dtype="float32")
    if X.shape[0] <= target_dim + 1:
        return X  # 样本太少，不降维
    try:
        import umap  # type: ignore
        reducer = umap.UMAP(n_components=min(target_dim, X.shape[0] - 2),
                             metric="cosine", random_state=42)
        return reducer.fit_transform(X).astype("float32")
    except Exception as e:  # noqa: BLE001
        logger.debug("UMAP 不可用，回退 PCA：%r", e)
    try:
        from sklearn.decomposition import PCA
        n = min(target_dim, max(2, X.shape[0] - 1), X.shape[1])
        pca = PCA(n_components=n, random_state=42)
        return pca.fit_transform(X).astype("float32")
    except Exception as e:  # noqa: BLE001
        logger.warning("PCA 也失败，使用原始高维向量：%r", e)
        return X


def _optimal_n_clusters(n_samples: int, target_size: int = 5) -> int:
    """根据样本数 & 目标簇大小估算簇数。"""
    import math
    if n_samples <= 2:
        return 1
    est = max(1, int(math.ceil(n_samples / max(2, target_size))))
    # 经验：簇数最多不超过 sqrt(n)，避免过度碎片化
    upper = max(2, int(math.sqrt(n_samples)))
    return max(1, min(est, upper))


def gmm_cluster(
    vectors, target_cluster_size: int = 5,
) -> Tuple[List[int], int]:
    """返回 (每条样本的簇标签, 总簇数)。

    单样本 / 低维场景直接返回 [0, 0, ...]；上层根据"簇数 == 1"决定是否继续递归。
    """
    import numpy as np
    n = len(vectors or [])
    if n <= 1:
        return ([0] * n, max(1, n))
    reduced = _reduce_dim(vectors, target_dim=10)
    k = _optimal_n_clusters(n, target_size=target_cluster_size)
    if k <= 1:
        return ([0] * n, 1)
    try:
        from sklearn.mixture import GaussianMixture
        gmm = GaussianMixture(n_components=k, random_state=42, max_iter=80)
        labels = gmm.fit_predict(reduced).tolist()
    except Exception as e:  # noqa: BLE001
        logger.warning("GMM 失败，回退 KMeans：%r", e)
        try:
            from sklearn.cluster import KMeans
            km = KMeans(n_clusters=k, random_state=42, n_init=5)
            labels = km.fit_predict(reduced).tolist()
        except Exception as e2:  # noqa: BLE001
            logger.warning("KMeans 也失败，全部归到 0 号簇：%r", e2)
            return ([0] * n, 1)
    labels = [int(x) for x in labels]
    distinct = len(set(labels))
    return labels, max(1, distinct)
