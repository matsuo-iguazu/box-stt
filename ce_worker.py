### ce_worker.py
import os
import sys
import io
import time
from dotenv import load_dotenv
from ibm_watson import SpeechToTextV1
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from box_sdk_gen import (
    BoxClient, BoxCCGAuth, CCGConfig, 
    UploadFileAttributes, UploadFileAttributesParentField, UploadFileVersionAttributes
)
# 共通ログを読み込み
from ce_utils import ce_log

load_dotenv()

def get_clients():
    box_config = CCGConfig(
        client_id=os.getenv('BOX_CLIENT_ID'),
        client_secret=os.getenv('BOX_CLIENT_SECRET'),
        enterprise_id=os.getenv('BOX_ENTERPRISE_ID')
    )
    box_client = BoxClient(BoxCCGAuth(box_config))
    stt_auth = IAMAuthenticator(os.getenv('STT_API_KEY'))
    stt = SpeechToTextV1(authenticator=stt_auth)
    stt.set_service_url(os.getenv('STT_SERVICE_URL'))
    return box_client, stt

def find_existing_file(box, folder_id, filename):
    items = box.folders.get_folder_items(folder_id)
    for item in items.entries:
        if item.name == filename:
            return item.id
    return None

def main():
    # 引数の受け取り (Receiverから渡される)
    if len(sys.argv) < 3:
        ce_log("WORKER", "!!! 引数不足", "Usage: ce_worker.py <file_id> <file_name>")
        return
        
    file_id = sys.argv[1]
    file_name = sys.argv[2]

    ce_log("WORKER", "1.処理開始", file_name)
    box, stt = get_clients()
    
    try:
        # 1. ダウンロード
        file_content = box.downloads.download_file(file_id)
        audio_data = io.BytesIO(file_content.read())
        # --- 追加: 拡張子判定による content_type 設定 ---
        _, ext = os.path.splitext(file_name)
        ext = ext.lower()
        if ext == '.mp3':
            content_type = 'audio/mp3'
        elif ext == '.wav':
            content_type = 'audio/wav'
        else:
            # 未対応の拡張子は警告して mp3 をデフォルトにする（既存挙動に合わせる）
            ce_log("WORKER", "WARN", f"Unknown extension '{ext}' for {file_name}, defaulting to audio/mp3")
            content_type = 'audio/mp3'

        # --- 追加: .env から STT_MODEL を取得（無ければ従来の ja-JP をフォールバック） ---
        stt_model = os.getenv('STT_MODEL') or 'ja-JP'

        # 2. Watsonジョブ作成
        ce_log("WORKER", "2.ジョブ作成", file_name)
        # create_job に渡すパラメータに content_type と model を反映
        audio_data.seek(0)
        job = stt.create_job(
            audio=audio_data, content_type=content_type,
            model=stt_model, results_ttl=120
        ).get_result()
        
        job_id = job['id']
        ce_log("WORKER", "3.ジョブ監視中", job_id)

        # 3. ポーリング
        while True:
            check = stt.check_job(job_id).get_result()
            status = check['status']
            if status == 'completed':
                results = check.get('results', [])
                transcript = "".join([res['alternatives'][0]['transcript'] for res in results[0]['results']]) if results else ""
                break
            elif status in ['failed', 'cancelled']:
                ce_log("WORKER", "!!! エラー終了", f"{file_name} (status: {status})")
                return
            time.sleep(10)

        # 4. テキスト保存
        text_filename = f"{os.path.splitext(file_name)[0]}.txt"
        text_stream = io.BytesIO(transcript.encode('utf-8'))
        text_folder_id = os.getenv('BOX_TEXT_FOLDER_ID')

        existing_id = find_existing_file(box, text_folder_id, text_filename)
        if existing_id:
            box.uploads.upload_file_version(file_id=existing_id, file=text_stream, attributes=UploadFileVersionAttributes(name=text_filename))
        else:
            box.uploads.upload_file(attributes=UploadFileAttributes(name=text_filename, parent=UploadFileAttributesParentField(id=text_folder_id)), file=text_stream)
        
        ce_log("WORKER", "4.テキスト保存", text_filename)

        # 5. ファイル移動
        #   移動先に同名ファイルがあった場合はエラーにせず、そのファイルに対して「新バージョンをアップロード」する。
        done_folder_id = os.getenv('BOX_DONE_FOLDER_ID')
        existing_done_id = find_existing_file(box, done_folder_id, file_name)

        # reset pointer for potential upload
        audio_data.seek(0)

        if existing_done_id:
            # 既存ファイルに対して新バージョンとして格納し、元ファイルは削除して「移動」相当とする
            box.uploads.upload_file_version(file_id=existing_done_id, file=audio_data, attributes=UploadFileVersionAttributes(name=file_name))
            # 元ファイルを削除して移動と同等にする
            try:
                box.files.delete_file_by_id(file_id)
            except Exception as e:
                # 削除に失敗しても致命的ではないのでログに残す
                ce_log("WORKER", "WARN", f"Failed to delete original file {file_id}: {str(e)}")
            ce_log("WORKER", "5.ファイル移動(既存にバージョン追加)", file_name)
        else:
            # 既存ファイルがなければ従来通り移動
            box.files.update_file_by_id(file_id, parent={"id": done_folder_id})
            ce_log("WORKER", "5.ファイル移動", file_name)

        # 6. 処理完了
        ce_log("WORKER", "6.処理完了", file_name)

    except Exception as e:
        ce_log("WORKER", "!!! 異常発生", f"{file_name} ({str(e)})")

if __name__ == '__main__':
    main()
