# 察元 · Kubernetes Helm Chart

## 何时用

* **企业 / 多租户场景**:多人共享一套察元、需要 HPA / Ingress / RBAC / 高可用。
* **不要用于个人单机**:单机直接用安装包(`packaging/`),不必上 k8s。

## 快速安装

```bash
# 1. 准备外部依赖(假设你已有 postgres / redis / milvus / minio)
#    把它们的连接字符串写到 my-values.yaml,见 values.yaml 顶部注释。

# 2. 安装
helm install chayuan ./deploy/k8s \
  -n chayuan --create-namespace \
  -f my-values.yaml

# 3. 等 pod ready
kubectl -n chayuan get pods
```

## 默认部署

| 组件 | 副本 | HPA | 备注 |
|---|---|---|---|
| chayuan-gateway | 3 | 3 → 12 | OpenAI 兼容 API + 模型路由 |
| chayuan-server  | 2 | (无) | NiceGUI 配置面板 + 业务 API |

外接(默认假设你有,helm 不部署):

* PostgreSQL (业务数据 + KB metadata)
* Redis (缓存 / pubsub / lifecycle store)
* Milvus (向量库)
* MinIO (文件存储)
* OnlyOffice (文档协同)

如果这些你还没起,设 `<svc>.deploy.enabled=true` 让 helm 顺便起一份。需要先:

```bash
helm dependency update ./deploy/k8s
```

## 密钥管理

* 默认 values.yaml 里有 `CHANGE_ME`占位的 password / secret;**生产环境必须覆盖**。
* 推荐做法:用 `--set-file postgres.url=<file>` 或 `external-secrets-operator` 注入。

## 升级路径

```bash
# 升级到新版本
helm upgrade chayuan ./deploy/k8s -n chayuan -f my-values.yaml

# 回滚(若新版本有 bug)
helm rollback chayuan -n chayuan
```

## 已知限制

* GPU 调度本 chart 不管;请用上游 NVIDIA gpu-operator + 在 values 里加 `tolerations` / `nodeSelector`。
* 跨集群联邦(multi-region)未做;v6.2 计划加 `Submariner` 集成。
* 镜像仓库内网 mirror 通过 `global.registry` 切换;但 `imagePullSecrets` 需自行准备。
