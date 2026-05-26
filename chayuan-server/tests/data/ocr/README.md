# OCR 黄金 fixture

> 这里放 OCR 评测用的"标准证件 / 发票"图像 + 期望识别字段。
> 真实图像不进 git(避免人脸/账户敏感信息);只进 `expected.json`。

## 目录约定

```
tests/data/ocr/
├── README.md              # 本文件
├── expected.json          # 标注 (filename → 期望字段)
├── id_card.png            # 用户自备 (不进 git;.gitignore 已忽略)
├── invoice.png            # 用户自备 (不进 git)
└── handwriting.png        # 用户自备 (不进 git)
```

## expected.json 结构

```json
{
  "id_card.png": {
    "must_contain": ["姓名", "身份证号"],
    "must_not_contain": [],
    "min_length": 30
  },
  "invoice.png": {
    "must_contain": ["发票", "金额", "税号"],
    "min_length": 50
  }
}
```

* `must_contain`: 识别文本里**必须**包含的关键字 (不区分大小写)
* `must_not_contain`: 识别文本里**不应**出现的关键字 (比如水印、噪声字)
* `min_length`: 识别文本至少这么长 (避免 OCR 完全失败时通过)

## 如何运行

```bash
pytest tests/test_ocr_golden.py -v
```

如果对应图像文件缺失,测试会被 `pytest.skip` 跳过(而不是失败);这样
开发者本地可以选择性放图,CI 看到 fixture 缺就明确跳过,不会假阳性绿。

## 添加新 case

1. 准备图像放到 `tests/data/ocr/<name>.png`(不进 git)。
2. 在 `expected.json` 加一条标注。
3. `pytest tests/test_ocr_golden.py -v -k <name>` 验证通过。
4. 如果是高价值 case,联系运维把图像加密放到 S3 / OSS,本地 `chayuan dev:fetch-ocr-fixtures` 拉。
