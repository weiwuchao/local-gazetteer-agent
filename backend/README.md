# backend

FastAPI 单进程应用（`--workers 1` 是硬约束，见架构文档 D-1）。

## 分层与依赖方向

```
api → services → repo / agent / rag / workers / llm → core
```

- `api` 只做协议适配、鉴权依赖、SSE 编码、错误映射。
- `services` 是业务规则与事务边界的唯一位置。
- `agent` 不写 SQL、不碰 IO，只做 8 阶段编排与守卫。
- `rag` 只接收 `SearchScope`，不自行判断权限。
- `repo` 是 SQL 的唯一出口。
- `workers` 只管领取/租约/幂等/重试，业务委托 `services`。

## 启动

```bash
python -m pip install <pkg>
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1 --reload
```

本机没有 poetry，解释器用父目录的 venv `E:\agent_code\.venv`（详见 `../AGENTS.md` 的「Python 环境」），依赖清单见 `../pyproject.toml`。从零装依赖时，本链路至少需要：

```bash
python -m pip install fastapi uvicorn pydantic-settings chromadb jieba \
  langchain-core
```

当前仍是脚手架：路由已注册，业务函数多为 `NotImplementedError` 占位。
已实现的是 P0 的**入库链路**：解析 → 按长度切块 → `chunk`/FTS5 → bge-m3 → Chroma。

## 语料入库

语料默认放在仓库根的 `doc/语料素材/`，一个文件对应一篇文档。支持 `.md` / `.txt` /
`.pdf` / `.docx`；PDF 必须有文本层（扫描件需要 OCR，本链路不做）。PDF/DOCX 抽取需要
`python -m pip install "pymupdf<2" python-docx`。

```bash
# 扫描 doc/语料素材，切块 + 向量化 + 置为已发布（缺依赖/模型直接报错，不降级）
python scripts/ingest_corpus.py
```

指定单个文件、县市与标题：

```bash
python scripts/ingest_corpus.py ../doc/语料素材/县情概述.md \
  --region 扶风县 --title 扶风县志
```

结果写入：

- `data/app.db` 的 `region` / `document` / `document_version` / `chunk`（FTS5 触发器自动维护）；
- `data/chroma/chunks__{index_version}` 的向量、原文与元数据；
- `document.chunk_count`、`document.active_index_version`、`chunk.index_state`。

> 本项目不做增量迁移：改过表结构后直接删除 `backend/data/app.db` 与
> `backend/data/chroma` 重新入库（AGENTS.md 的约定）。

## 缺依赖 / 缺模型时

不做降级：`rag/models.py`、`rag/chroma_store.py` 直接抛 `RagUnavailable`，异常文本只给一句原因，
按下面装好后重跑入库命令即可。

```bash
# Chroma（嵌入式，无需单独起服务）
python -m pip install chromadb

# 推理后端与模型加载库（CPU 版 torch）
python -m pip install torch --index-url https://download.pytorch.org/whl/cpu
python -m pip install sentence-transformers sentencepiece
```

> **Windows 上 `torch` 与 `Chroma` 的原生库都要 VC++ 运行库 14.44+**。本机实测两个
> 症状同源，都是运行库过旧：
>
> - `import torch` → `OSError: [WinError 126] ... torch\lib\shm.dll`：`torch_cpu.dll`
>   依赖 `VCRUNTIME140_THREADS.dll`，这个 DLL 由运行库 **14.44** 才引入。
> - Chroma 第一次 `upsert` → 进程级 access violation（`0xc0000005`，无 traceback，
>   Python 抓不到）：事件日志报错模块为 `MSVCP140.dll`。
>
> 判断当前版本：`Get-Item C:\Windows\System32\vcruntime140_threads.dll`，不存在就是
> 低于 14.44；装过 14.30/14.32 也照样报错，必须升级。修复：
> <https://aka.ms/vs/17/release/vc_redist.x64.exe>，装完**重启终端**，再执行
> `python scripts/ingest_corpus.py`（`upsert` 不崩即通过）。

> 只有升到 14.44 后 Chroma 仍崩，才怀疑 CPU 或预编译库本身：改在 WSL2/Linux 运行，
> 或安装 MSVC Build Tools 后使用 `chromadb==0.5.23`。本项目不做降级，环境没修好
> 入库会直接失败。

### 下载 bge-m3（任选一种）

1. **ModelScope（国内推荐）**

   ```bash
   python -m pip install modelscope
   modelscope download --model BAAI/bge-m3 --local_dir data/models/bge-m3
   # 把 app_config 指向本地目录
   python -c "from app.repo import db, app_config_repo; db.init(); app_config_repo.set_value('llm.embedding.model', 'data/models/bge-m3'); db.shutdown()"
   ```

2. **HuggingFace 官方**

   ```bash
   python -m pip install huggingface_hub
   huggingface-cli download BAAI/bge-m3 \
     --revision 5617a9f61b028005a4858fdac845db406aefb181 \
     --cache-dir data/models
   ```

3. **HuggingFace 镜像**

   ```powershell
   $env:HF_ENDPOINT="https://hf-mirror.com"
   huggingface-cli download BAAI/bge-m3 `
     --revision 5617a9f61b028005a4858fdac845db406aefb181 `
     --cache-dir data/models
   ```

4. **浏览器手动下载**：把模型全部文件放到 `data/models/bge-m3/`，至少需要
   `config.json`、`config_sentence_transformers.json`、`sentence_bert_config.json`、
   `modules.json`、`1_Pooling/config.json`、`tokenizer.json`、`tokenizer_config.json`、
   `special_tokens_map.json`、`sentencepiece.bpe.model`，以及
   `pytorch_model.bin` 或 `model.safetensors`（约 2.2 GB）。

地址汇总：

- HuggingFace：<https://huggingface.co/BAAI/bge-m3>
- HF 镜像：<https://hf-mirror.com/BAAI/bge-m3>
- ModelScope：<https://modelscope.cn/models/BAAI/bge-m3>
- Chroma：<https://pypi.org/project/chromadb/>

安装完成后：

```bash
python scripts/ingest_corpus.py    # 会加载模型并入库；缺什么会直接报错
```

> 模型与索引版本绑定：换模型 / 维度 / 块大小 = 新建 collection + 删库重灌
> （详细设计 4.7.5）。`chunk` 表是真相源，Chroma 可丢可重建。

## 测试

```bash
python -m pytest tests -q
ruff check .
```

测试使用 `tmp_path` 临时库与临时 Chroma/假 collection，不读写 `data/app.db`。
