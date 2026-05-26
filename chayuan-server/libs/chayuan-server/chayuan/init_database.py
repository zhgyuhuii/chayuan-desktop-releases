# Description: 初始化数据库，包括创建表、导入数据、更新向量空间等操作
#
# 40 题 P0:把 ``chayuan.server.knowledge_base.migrate`` 链 lazy 化。
# 那条链 eager import 会拖出 ``langchain_text_splitters → sentence_transformers
# → transformers → torch``,实测加起来 ≈ 3.6 秒。``chayuan.cli`` 顶部为了能
# ``from chayuan.init_database import main, create_tables, folder2db`` 而 import
# 本模块,但**绝大多数 cli 子命令(start / stop / restart / service / model
# / status / doctor / ...)根本不调 create_tables/folder2db**,所以让它们以
# lazy wrapper 的形式存在 — 公开接口零变动,模块 import 时不再触发 ML 重链。
from datetime import datetime
import multiprocessing as mp
import sys
import time
from typing import Dict

import click

from chayuan.utils import build_logger


logger = build_logger()


# ---------------------------------------------------------------------------
# Lazy wrappers — 保持公开接口与原 ``from migrate import xxx`` 一致,
# 但只在被**调用**时(而非 import 时)触发 migrate 模块加载。
# ---------------------------------------------------------------------------

def create_tables(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import create_tables as _impl
    return _impl(*args, **kwargs)


def folder2db(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import folder2db as _impl
    return _impl(*args, **kwargs)


def import_from_db(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import import_from_db as _impl
    return _impl(*args, **kwargs)


def prune_db_docs(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import prune_db_docs as _impl
    return _impl(*args, **kwargs)


def prune_folder_files(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import prune_folder_files as _impl
    return _impl(*args, **kwargs)


def reset_tables(*args, **kwargs):
    from chayuan.server.knowledge_base.migrate import reset_tables as _impl
    return _impl(*args, **kwargs)


def worker(args: dict):
    start_time = datetime.now()

    try:
        if args.get("create_tables"):
            create_tables()  # confirm tables exist

        if args.get("clear_tables"):
            reset_tables()
            print("database tables reset")

        if args.get("recreate_vs"):
            create_tables()
            print("recreating all vector stores")
            folder2db(
                kb_names=args.get("kb_name"), mode="recreate_vs", embed_model=args.get("embed_model")
            )
        elif args.get("import_db"):
            import_from_db(args.get("import_db"))
        elif args.get("update_in_db"):
            folder2db(
                kb_names=args.get("kb_name"), mode="update_in_db", embed_model=args.get("embed_model")
            )
        elif args.get("increment"):
            folder2db(
                kb_names=args.get("kb_name"), mode="increment", embed_model=args.get("embed_model")
            )
        elif args.get("prune_db"):
            prune_db_docs(args.get("kb_name"))
        elif args.get("prune_folder"):
            prune_folder_files(args.get("kb_name"))

        end_time = datetime.now()
        print(f"总计用时\t：{end_time-start_time}\n")
    except Exception as e:
        logger.exception(e)


@click.command(help="知识库相关功能")
@click.option(
        "-r",
        "--recreate-vs",
        is_flag=True,
        help=(
            """
            recreate vector store.
            use this option if you have copied document files to the content folder, but vector store has not been populated or DEFAUL_VS_TYPE/DEFAULT_EMBEDDING_MODEL changed.
            """
        ),
)
@click.option(
        "--create-tables",
        is_flag=True,
        help=("create empty tables if not existed"),
)
@click.option(
        "--clear-tables",
        is_flag=True,
        help=(
            "create empty tables, or drop the database tables before recreate vector stores"
        ),
)
@click.option(
        "-u",
        "--update-in-db",
        is_flag=True,
        help=(
            """
            update vector store for files exist in database.
            use this option if you want to recreate vectors for files exist in db and skip files exist in local folder only.
            """
        ),
)
@click.option(
        "-i",
        "--increment",
        is_flag=True,
        help=(
            """
            update vector store for files exist in local folder and not exist in database.
            use this option if you want to create vectors incrementally.
            """
        ),
)
@click.option(
        "--prune-db",
        is_flag=True,
        help=(
            """
            delete docs in database that not existed in local folder.
            it is used to delete database docs after user deleted some doc files in file browser
            """
        ),
)
@click.option(
        "--prune-folder",
        is_flag=True,
        help=(
            """
            delete doc files in local folder that not existed in database.
            is is used to free local disk space by delete unused doc files.
            """
        ),
)
@click.option(
        "-n",
        "--kb-name",
        multiple=True,
        default=[],
        help=(
            "specify knowledge base names to operate on. default is all folders exist in KB_ROOT_PATH."
        ),
)
@click.option(
        "-e",
        "--embed-model",
        type=str,
        default=None,
        help=("specify embeddings model. default: 取自 settings 的 DEFAULT_EMBEDDING_MODEL"),
)
@click.option(
        "--import-db",
        help="import tables from specified sqlite database"
)
def main(**kwds):
    # 40 题 P0:--embed-model 默认值原本是 ``default=get_default_embedding()``,
    # click 在 import 时就求值,会触发 ``chayuan.server.utils`` 重链。改成
    # 默认 None,在 callback 内按需 lazy 求值。
    if not kwds.get("embed_model"):
        from chayuan.server.utils import get_default_embedding
        kwds["embed_model"] = get_default_embedding()
    p = mp.Process(target=worker, args=(kwds,), daemon=True)
    p.start()
    while p.is_alive():
        try:
            time.sleep(0.1)
        except KeyboardInterrupt:
            logger.warning("Caught KeyboardInterrupt! Setting stop event...")
            p.terminate()
            sys.exit()


if __name__ == "__main__":
    mp.set_start_method("spawn")
    main()
