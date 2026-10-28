import argparse
import datetime as dt
import hashlib
import json
import logging
import os
import smtplib
import ssl
from email.message import EmailMessage
from pathlib import Path
from .core import KST, SendLedger, build, save


def main():
    parser = argparse.ArgumentParser(description='한국 시장 보고서. 기본 동작은 파일 미리보기만 생성합니다.')
    parser.add_argument('--output', default='reports')
    parser.add_argument('--state', default='state')
    parser.add_argument('--send', action='store_true')
    parser.add_argument('--confirm-live-send', action='store_true')
    args = parser.parse_args()
    if args.send and not args.confirm_live_send:
        parser.error('실제 발송에는 --send --confirm-live-send 둘 다 필요합니다.')
    Path(args.state).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=str(Path(args.state)/'runs.log'), level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    now = dt.datetime.now(KST)
    logging.info('run started %s', now.isoformat())
    prior_files = sorted(Path(args.output).glob('*/*/data.json'))
    previous = json.loads(prior_files[-1].read_text()) if prior_files else None
    report = build(now, previous)
    target = save(report, args.output)
    for name, item in {**report['markets'], 'news': report['news']}.items():
        if item['status'] == 'missing':
            logging.warning('collection failure provider=%s reason=%s', name, item['reason'])
    print('미리보기:', target/'email-preview.html')
    logging.info('report saved %s', target)
    if not args.send:
        return
    # Never send a failed or incomplete market report silently.
    if any(v['status'] != 'observed' for v in report['markets'].values()) or report['news']['status'] != 'observed':
        raise SystemExit('필수 수집 실패로 발송 중단. 미리보기에서 확인하세요.')
    required = ('REPORT_TO','SMTP_HOST','SMTP_USER','SMTP_PASSWORD','REPORT_FROM')
    absent = [k for k in required if not os.environ.get(k)]
    if absent:
        raise SystemExit('환경변수 누락: '+', '.join(absent))
    ledger = SendLedger(Path(args.state)/'deliveries.sqlite3')
    # One delivery per KST calendar day and recipient, even if report is regenerated.
    key = hashlib.sha256((now.date().isoformat()+'|'+os.environ['REPORT_TO'].strip().lower()).encode()).hexdigest()
    if not ledger.reserve(key):
        raise SystemExit('이미 발송했거나 결과가 불확실한 발송 기록이 있습니다. 자동 재발송하지 않습니다.')
    message = EmailMessage()
    message['Subject'] = f'한국 시장 일일 보고서 {now.date()}'
    message['From'], message['To'] = os.environ['REPORT_FROM'], os.environ['REPORT_TO']
    message.set_content('HTML 보고서를 확인하세요. 투자 추천이 아닙니다.')
    message.add_alternative((target/'email-preview.html').read_text(), subtype='html')
    try:
        with smtplib.SMTP_SSL(os.environ['SMTP_HOST'], int(os.getenv('SMTP_PORT','465')),
                              context=ssl.create_default_context(), timeout=30) as server:
            server.login(os.environ['SMTP_USER'], os.environ['SMTP_PASSWORD'])
            refused = server.send_message(message)
            if refused:
                raise RuntimeError('recipient refused')
        ledger.finish(key,'sent')
        logging.info('delivery sent id=%s', key)
    except Exception as exc:
        # SMTP acceptance may have occurred before a disconnect. Keep reservation.
        ledger.finish(key,'failed_or_uncertain')
        logging.error('delivery failed id=%s exception=%s', key, type(exc).__name__)
        raise SystemExit('발송 실패 또는 결과 불명. 중복 방지를 위해 자동 재시도하지 않습니다.') from None
