# 広告設計支援アプリ 試作品1A

Google Ads APIとOpenAI APIをまだ接続せず、仮データで業務フローを確認するローカルWebアプリです。

## できること

- 広告プロジェクトの作成、保存、再開
- 複数キャンペーンの管理
- ターゲット、除外ターゲット、除外キーワードの設定
- 広告グループとキーワード候補の試作生成
- Keyword Plannerを模した仮データの表示
- マッチタイプの変更とコピー用表記の自動更新
- 日予算、参考CPC、キャンペーン分割の試作提案
- 見出し15件、説明文4件の試作生成と文字数確認
- 広告プロジェクト全体のWord出力

## 起動方法

いちばん簡単な方法は、`start_app.bat` をダブルクリックすることです。初回だけ必要な部品を自動でインストールするため、数分かかる場合があります。

起動スクリプトは通常のPythonに加え、Codexに同梱されたPythonも探します。どちらも見つからない場合だけ、Python 3.11以上をインストールしてください。

### PowerShellから起動する場合

PowerShellでこのフォルダを開き、次を実行します。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py
```

ブラウザが自動で開かない場合は、ターミナルに表示される `http://localhost:8501` を開きます。

## 注意

- 画面の検索数、競合性、bidは試作用データです。
- 現段階では実際のGoogle Adsアカウントへ接続しません。
- 現段階ではOpenAI APIを使わず、決められたサンプル候補を生成します。
- APIキーやGoogleの認証情報をファイルへ直接書かないでください。
