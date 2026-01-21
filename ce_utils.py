# ce_utils.py
import os
import io
import datetime
from datetime import timezone, timedelta
from box_sdk_gen import (
    BoxClient, BoxCCGAuth, CCGConfig,
    UploadFileAttributes, UploadFileAttributesParentField, UploadFileVersionAttributes
)

def get_jst_now():
    return datetime.datetime.now(
        timezone(timedelta(hours=9))
    ).strftime('%Y-%m-%d %H:%M:%S')

def _get_box_client():
    box_config = CCGConfig(
        client_id=os.getenv('BOX_CLIENT_ID'),
        client_secret=os.getenv('BOX_CLIENT_SECRET'),
        enterprise_id=os.getenv('BOX_ENTERPRISE_ID')
    )
    return BoxClient(BoxCCGAuth(box_config))

def _find_existing_file(box, folder_id, filename):
    items = box.folders.get_folder_items(folder_id)
    for item in items.entries:
        if item.name == filename:
            return item.id
    return None

def _upload_log_to_box(line: str):
    """Boxにログを書き込む（バージョン管理あり）"""
    try:
        box = _get_box_client()
        folder_id = os.getenv('BOX_DONE_FOLDER_ID')
        log_name = "box-stt.log"

        log_stream = io.BytesIO(line.encode("utf-8"))
        existing_id = _find_existing_file(box, folder_id, log_name)

        if existing_id:
            box.uploads.upload_file_version(
                file_id=existing_id,
                file=log_stream,
                attributes=UploadFileVersionAttributes(name=log_name)
            )
        else:
            box.uploads.upload_file(
                attributes=UploadFileAttributes(
                    name=log_name,
                    parent=UploadFileAttributesParentField(id=folder_id)
                ),
                file=log_stream
            )
    except Exception:
        # 本処理を止めない
        pass

def ce_log(role, step_message, target):
    """標準出力＋必要に応じてBoxログ"""
    now = get_jst_now()
    line = f"[{now}] [{role}] {step_message}：{target}\n"

    # 標準出力（常に）
    print(line, flush=True)

    # Boxログは環境変数 CE_BOX_LOG_ENABLED=true の時だけ
    if os.getenv("CE_BOX_LOG_ENABLED", "false").lower() == "true":
        _upload_log_to_box(line)
