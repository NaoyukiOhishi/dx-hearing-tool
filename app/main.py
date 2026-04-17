import os
import smtplib
from datetime import date
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

import anthropic
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

# .envファイルを読み込む
load_dotenv()

app = FastAPI()

# CORS設定（XserverのフロントエンドからAPIを呼び出せるようにする）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 本番環境ではXserverのドメインに限定すること
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

# フォームから受け取るデータの型を定義
class HearingForm(BaseModel):
    company_name: str       # 会社名・屋号
    contact_name: str       # 担当者名
    employee_count: str     # 従業員規模
    industry: str           # 業種
    current_tools: str      # 現在使っているシステム・ツール
    problems: str           # 困っていること（自由記述）
    email: str              # 連絡先メール

# staticフォルダのHTMLファイルを配信する
import os as _os
_static_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), "static")
app.mount("/static", StaticFiles(directory=_static_path, html=True), name="static")

# ヘルスチェック用エンドポイント（Render.comのスリープ対策pingにも使用）
@app.get("/health")
def health_check():
    return {"status": "ok"}

# ヒアリングフォームの送信を受け取り、レポートを生成するエンドポイント
@app.post("/api/hearing")
async def generate_report(form: HearingForm):
    # Claude APIクライアントを初期化
    client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

    # Claudeに渡すプロンプトを組み立てる
    prompt = f"""
あなたはDX（デジタルトランスフォーメーション）の専門コンサルタントです。
以下の中小企業の情報をもとに、IT・業務課題の整理レポートを作成してください。

【企業情報】
- 会社名・屋号: {form.company_name}
- 担当者名: {form.contact_name}
- 従業員規模: {form.employee_count}
- 業種: {form.industry}
- 現在使用しているシステム・ツール: {form.current_tools}
- 困っていること・解決したいこと: {form.problems}
- 本日の日付: {date.today().strftime("%Y年%m月%d日")}

以下の構成でレポートを作成してください。

## 1. 現状の課題整理
（入力内容をもとに、課題を3〜5点に整理・分類する）

## 2. 優先度の高い課題
（最も緊急性・重要性が高いものを1〜2点ピックアップし、その理由を説明）

## 3. 改善の方向性
（各課題に対して、具体的な改善の方向性を提示する）

## 4. 次のアクション提案
（まず最初に取り組むべき具体的なステップを3点提示する）

## 5. まとめ
（全体を2〜3行で簡潔にまとめる）

※ 専門用語はできるだけ避け、経営者が読みやすい平易な日本語で記述してください。
"""

    # Claude APIを呼び出してレポートを生成
    message = client.messages.create(
        model="claude-opus-4-5",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    report_text = message.content[0].text

    # クライアントにメールアドレスが入力されていれば、レポートを送信する
    if form.email:
        send_report_email(form.company_name, form.contact_name, form.email, report_text)

    # 大石さん（オーナー）に新規ヒアリングの通知メールを送る
    send_owner_notification(form, report_text)

    return {
        "status": "success",
        "report": report_text
    }


def send_owner_notification(form: HearingForm, report_text: str):
    """新規ヒアリングが届いたことをオーナー（大石さん）に通知する"""
    gmail_address = os.getenv("GMAIL_ADDRESS")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    sender_name = os.getenv("SENDER_NAME", "Concha IT Nexus")
    # 通知先はGMAIL_ADDRESSと同じ（自分自身に送る）
    # 別のアドレスに送りたい場合は NOTIFY_EMAIL を環境変数に追加する
    notify_email = os.getenv("NOTIFY_EMAIL", gmail_address)

    # メール未設定の場合はスキップ
    if not gmail_address or not gmail_app_password:
        return

    # 件名：誰から届いたか一目でわかる形式
    subject = f"【新規ヒアリング】{form.company_name}｜{form.contact_name}様"

    # 本文：入力内容をすべて含める
    body = f"""新規ヒアリングが届きました。

━━━━━━━━━━━━━━━━━━━━━━
【入力内容】

会社名・屋号　: {form.company_name}
担当者名　　　: {form.contact_name}
従業員規模　　: {form.employee_count}
業種　　　　　: {form.industry}
使用ツール　　: {form.current_tools if form.current_tools else "（未入力）"}
連絡先メール　: {form.email if form.email else "（未入力）"}

【困っていること・解決したいこと】
{form.problems}

━━━━━━━━━━━━━━━━━━━━━━
【生成されたレポート】

{report_text}

━━━━━━━━━━━━━━━━━━━━━━
{sender_name}
"""

    # MIMEメッセージを作成
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{gmail_address}>"
    msg["To"] = notify_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # GmailのSMTPサーバーで送信
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)
    except Exception as e:
        # 通知失敗してもレポート表示は続ける（ログだけ残す）
        print(f"オーナー通知メール送信エラー: {e}")


def send_report_email(company_name: str, contact_name: str, to_email: str, report_text: str):
    """レポートをメールで送信する"""
    gmail_address = os.getenv("GMAIL_ADDRESS")
    gmail_app_password = os.getenv("GMAIL_APP_PASSWORD")
    sender_name = os.getenv("SENDER_NAME", "Concha IT Nexus")

    # メールの件名と本文を作成
    subject = f"【IT課題整理レポート】{company_name} 様"
    body = f"""{contact_name} 様（{company_name}）

この度はIT課題ヒアリングフォームにご入力いただき、ありがとうございます。
以下に、現状の課題整理レポートをお送りします。

ご不明な点やさらに詳しくご相談されたい場合は、
お気軽にご返信ください。

━━━━━━━━━━━━━━━━━━━━━━

{report_text}

━━━━━━━━━━━━━━━━━━━━━━

{sender_name}
"""

    # MIMEメッセージを作成
    msg = MIMEMultipart()
    msg["From"] = f"{sender_name} <{gmail_address}>"
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.attach(MIMEText(body, "plain", "utf-8"))

    # GmailのSMTPサーバーで送信
    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
            server.login(gmail_address, gmail_app_password)
            server.send_message(msg)
    except Exception as e:
        # メール送信失敗してもレポート表示は続ける（ログだけ残す）
        print(f"メール送信エラー: {e}")
