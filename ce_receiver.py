import os
import inspect
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from ibm_cloud_sdk_core.authenticators import IAMAuthenticator
from ibm_code_engine_sdk.code_engine_v2 import CodeEngineV2
from ce_utils import ce_log # 共通ログをインポート

load_dotenv()
app = Flask(__name__)

# --- Code Engine Jobをキックする関数 ---
def kick_ce_job(file_id, file_name):
    try:
        # 1. 認証 (Secretsから読み込む想定)
        authenticator = IAMAuthenticator(os.getenv('IBM_CLOUD_API_KEY'))
        ce_service = CodeEngineV2(authenticator=authenticator)
        # 環境変数からURLを取得、なければデフォルトのPrivate URL
        # 末尾の /v2 を忘れずに付与する形にするのが安全です
        base_url = os.getenv('CE_API_BASE_URL', 'https://api.private.us-south.codeengine.cloud.ibm.com')
        if not base_url.endswith('/v2'):
            base_url = f"{base_url.rstrip('/')}/v2"
            
        ce_service.set_service_url(base_url)

        # 2. Jobの実行依頼
        # ※ job_name は後でCode Engine上で作成する名前に合わせます
        # 引数の1番目(argv[0]の次))にJobを実行させるスクリプト名を入れる
        job_arguments = [
            "ce_worker.py",
            str(file_id),
            str(file_name)
        ]

        job_run = ce_service.create_job_run(
            project_id=os.getenv('CE_PROJECT_ID'),
            job_name="stt-worker-job", 
            run_arguments=job_arguments
        ).get_result()
        
        return job_run['id']
    except Exception as e:
        ce_log("RECEIVER", "!!! Job起動失敗", str(e))
        return None

@app.route('/webhook', methods=['POST'])
def handle_webhook():
    data = request.json
    source = data.get('source', {})
    file_id = source.get('id')
    file_name = source.get('name')
    trigger = data.get('trigger')

    ce_log("RECEIVER", "1.Webhook受信", file_name if file_name else "Unknown")

    if trigger != 'FILE.UPLOADED':
        return jsonify({"status": "ignored"}), 200

    if file_id and file_name and file_name.lower().endswith('.mp3'):
        ce_log("RECEIVER", "2.WORKER起動依頼", file_name)
        
        # 本物のCode Engine Jobをキック！
        job_run_id = kick_ce_job(file_id, file_name)
        
        if job_run_id:
            ce_log("RECEIVER", "3.Job受付完了", f"RunID: {job_run_id[:8]}...")
            return jsonify({"status": "accepted", "job_run_id": job_run_id}), 202
        else:
            return jsonify({"status": "error", "message": "Failed to start job"}), 500
    
    return jsonify({"status": "ignored"}), 200

if __name__ == '__main__':
    # 環境変数 PORT があればそれを使い、なければ 8080 を使う
    port = int(os.environ.get("PORT", 8080))
    ce_log("RECEIVER", "システム起動", f"Port:{port} / JST")
    app.run(host='0.0.0.0', port=port)
