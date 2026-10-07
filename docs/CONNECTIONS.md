# サービス連携の使い方

左下の「サービス・外部AI」を開きます。接続は任意です。通常の会話・コード修正は引き続きローカルモデルを使い、接続しなくても利用できます。

検索条件や質問の送信先を確認して取得し、結果画面で添付内容を確認・編集してから「この内容を会話に添付」を押します。入力中の下書きを残して追記します。添付だけではAIへの質問は送信されません。

## GitHub

[Fine-grained PAT](https://github.com/settings/personal-access-tokens)を作り、対象リポジトリを限定します。参照には必要な読み取り権限、Issue投稿には対象リポジトリのIssues書き込み権限を選びます。「接続設定を開く」でトークンを登録します。登録時に`/user`で本人を確認します。

空の検索では最近更新したリポジトリ一覧、検索語を入れるとIssue/PR検索です。例: `repo:owner/repo is:open`。結果にはIssue本文を含みます。PRのコード差分やリポジトリ全文を自動取得する機能ではありません。

Issue投稿は「Issue・メール下書き」でowner/repo、件名、本文を入力し、プレビューしてから確認欄にチェックします。確定した内容だけを一度投稿します。期限切れや失敗後は保存先を確認してから新しいプレビューを作成してください。

## Google: Gmail・カレンダー・Drive・YouTube

初回は[公式のデスクトップOAuth手順](https://developers.google.com/identity/protocols/oauth2/native-app)に従い、自分のGoogle Cloudプロジェクトを用意します。

1. Gmail API、Google Calendar API、Google Drive API、YouTube Data API v3のうち使うAPIを有効にします。
2. OAuth同意画面を設定します。テスト中は自分のアカウントをテストユーザーに登録します。権限やアプリの公開状態によってGoogle側の確認・審査が必要です。
3. OAuthクライアントの種類は「デスクトップアプリ」。JSONをダウンロードし、「初回のGoogle接続設定」で貼り付けて登録します。
4. 利用するサービスを選択し、「Googleの認証画面を表示」から追加されたリンクを5分以内に開いて本人が認証します。
5. Local Deskに戻り「接続状態を更新」。許可されたサービスだけが認証済みになります。テスト状態のGoogleアプリでは更新トークンが短期間で失効する場合があり、その際は再接続します。

Gmailは検索（例: `is:unread newer_than:7d`）、件名・送信元・日付・プレーンテキスト本文。HTMLだけの本文と添付ファイルは取得しません。カレンダーは今後の予定、Driveは名前・種類・説明などファイル情報（本文は未取得）、YouTubeは検索結果の公開タイトル/説明または自分のチャンネルです。各一覧は最大10件を取得します。

Gmail下書きを保存する場合だけ「Gmailの下書き保存も許可する」を選んで再認証します。Googleの`gmail.compose`スコープ自体は送信も含みますが、このアプリには送信API操作がありません。プレビュー後の確認で`drafts`に保存します。送信はGmail側で本人が行ってください。

Googleサービスは共通の認証情報を使うため、接続解除はまとめて行います。アプリの「解除」はこのPCからトークンを削除します。Google側の許可を取り消す場合はGoogleアカウントの接続設定で操作してください。

## Notion

[Notion Integration](https://www.notion.so/profile/integrations)を作成してトークンを登録し、対象ページをそのIntegrationに共有します。現在の検索結果はページ名とURLです。ページ本文は取得していません。

## 動画リンク・Instagram・TikTok

動画リンクはアカウント接続なしでも使用できます。YouTube/TikTokのoEmbed公開タイトル・投稿者情報を取得します。APIが拒否/停止している場合は、取得できなかったことを明示した参照リンクを返します。TikTok短縮URLはブラウザで開いて、`/@名前/video/数字`形式のURLを貼り付けます。

InstagramのリンクはURLのみで、視聴・文字起こし・内容取得を行いません。動画内容を相談するには、説明/字幕を貼り付けるか、手元の動画/音声を「資料・音声の取り込み」から読み込みます。対応する動画音声はMP4/MOV/MKV/WebM（50MB以下・5分以下）。画像からの映像分析は行いません。

アカウントAPI接続は次の条件を満たした公式トークンを登録します。ブラウザのログインCookieをコピーしません。

- Instagram: [Instagram API with Instagram Login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/)のプロアカウント（ビジネス/クリエイター）、開発者アプリ、`instagram_business_basic`権限。自分の投稿のキャプション・URLなどを取得します。一般アカウントの公式API接続は対象外です。
- TikTok: [Display API](https://developers.tiktok.com/docs/en/tiktok-api-v2-video-list)の開発者アプリと`video.list`権限。必要な審査を済ませて発行したアクセストークンで自分の動画一覧を取得します。アプリ内でTikTokのOAuth認証・トークン自動更新を行う機能はまだありません。期限切れの場合は新しいトークンを登録してください。

APIトークン登録だけでは、各サービスの開発者設定・審査を代行したことにはなりません。初回のデータ取得で利用可否を確認できます。

## ほかのAI

OpenAI/Gemini/ClaudeのAPIキーと利用可能なモデルIDを登録します。モデルIDを固定していないため、提供元で利用できるIDを確認してください。[OpenAI Responses](https://developers.openai.com/api/reference/responses/overview)、[Gemini API](https://ai.google.dev/api)、[Claude API](https://docs.anthropic.com/en/api/messages)を利用します。

「外部AIに聞く」の欄へ入力した質問だけを送り、Local Deskの履歴・ファイル・メールを自動で一緒に送りません。費用が発生する可能性を確認して実行します。クラウドAIのチャット契約とAPIは別のサービスです。無料・無制限を保証する機能ではありません。

## 保存と解除

認証情報は`.data/connections.enc`にWindows DPAPIで暗号化して保存します。同じWindowsユーザーで復号でき、別PCへの履歴バックアップには入れません。APIキー・OAuth JSON・トークンを会話やGitHubへ貼り付けないでください。

接続情報だけの読み込み失敗は既存履歴やモデルを変更しません。Windows以外での認証情報の永続保存は現在未対応です。
