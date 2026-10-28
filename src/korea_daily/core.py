from __future__ import annotations
import datetime as dt
import hashlib
import html
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo('Asia/Seoul')


def fetch(url, limit=2_000_000):
    req = urllib.request.Request(url, headers={'User-Agent': 'KoreaDaily/0.1 (personal research)'})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError('response exceeds size limit')
        return data


def missing(reason, source=None):
    return {'status': 'missing', 'reason': reason, 'source': source}


def index_history(symbol, now):
    url = 'https://fchart.stock.naver.com/sise.nhn?' + urllib.parse.urlencode(
        {'symbol': symbol, 'timeframe': 'day', 'count': '65', 'requestType': '0'})
    try:
        root = ET.fromstring(fetch(url))
        rows = []
        for item in root.iter('item'):
            p = item.attrib['data'].split('|')
            day = dt.datetime.strptime(p[0], '%Y%m%d').date()
            # Never treat today's possibly provisional quote as a completed session.
            if day < now.date():
                rows.append({'date': day.isoformat(), 'close': float(p[4]), 'volume': float(p[5])})
        rows.sort(key=lambda x: x['date'])
        if not rows:
            raise ValueError('no completed-session observations')
        result = {'status': 'observed', 'source': url, 'date': rows[-1]['date'],
                  'close': rows[-1]['close'], 'returns': {},
                  'freshness': 'unverified', 'note': '공식 거래일 달력 대조 전: 휴장/지연 여부 미확인',
                  'turnover': missing('제공 필드가 거래량이며 거래대금은 아님', url)}
        for n in (1, 5, 20):
            result['returns'][str(n)] = ((rows[-1]['close'] / rows[-n-1]['close'] - 1) * 100
                                         if len(rows) > n else None)
        return result
    except Exception as exc:
        return missing(str(exc), url)


def event_key(title):
    # Exact normalized headline only: do not claim semantic event deduplication.
    return re.sub(r'[^\w]', '', title.casefold())


def parse_news(data, now):
    start = now - dt.timedelta(hours=24)
    seen, result = set(), []
    for item in ET.fromstring(data).findall('./channel/item'):
        try:
            published = parsedate_to_datetime(item.findtext('pubDate', ''))
            if published.tzinfo is None or not start <= published <= now:
                continue
        except (ValueError, TypeError):
            continue
        title = item.findtext('title', '').strip()
        key = event_key(title)
        if not key or key in seen:
            continue
        seen.add(key)
        result.append({'title': title, 'published': published.isoformat(),
                       'source': item.findtext('source', '출처 미확인'),
                       'url': item.findtext('link', ''), 'event_key': key,
                       'body_status': 'not_collected', 'body': '',
                       'facts': '본문·공시 대조 전: 제목을 확인된 기업 사실로 간주하지 않음',
                       'selection_reason': '한국 증시 검색 결과의 최신순 후보; 중요도 의미 분석 미수행',
                       'industry_theme': '검증된 연결 없음', 'impact_path': '확인 전',
                       'market_reaction': '기사와 가격 움직임의 인과관계 미확인',
                       'counter_evidence': '본문 및 공시 확인 필요',
                       'follow_up': '관련 공시·실적·경제지표 확인 필요'})
    return sorted(result, key=lambda x: x['published'], reverse=True)


def collect_news(now):
    query = urllib.parse.urlencode({'q': '한국 증시 when:1d', 'hl': 'ko', 'gl': 'KR', 'ceid': 'KR:ko'})
    url = 'https://news.google.com/rss/search?' + query
    try:
        return {'status': 'observed', 'source': url, 'items': parse_news(fetch(url), now),
                'deduplication': '정규화한 동일 제목 중복만 제거. 동일 사건의 다른 제목은 미병합.'}
    except Exception as exc:
        return {**missing(str(exc), url), 'items': []}


def compare_return(prices, benchmark):
    result = {}
    for n in (1, 5, 20):
        if len(prices) <= n or len(benchmark) <= n:
            result[str(n)] = None
        else:
            result[str(n)] = {'return': (prices[-1]/prices[-n-1]-1)*100,
                              'excess': ((prices[-1]/prices[-n-1])-(benchmark[-1]/benchmark[-n-1]))*100}
    return result


def attention_change(count, prior_seven_days):
    if len(prior_seven_days) != 7:
        return None
    mean = sum(prior_seven_days) / 7
    return None if mean == 0 else (count / mean - 1) * 100


def build(now, previous=None):
    markets = {symbol: index_history(symbol, now) for symbol in ('KOSPI', 'KOSDAQ')}
    for symbol, value in markets.items():
        old = (previous or {}).get('markets', {}).get(symbol, {})
        value['reused'] = bool(value.get('date') and value.get('date') == old.get('date'))
        value['change_from_previous_report'] = (
            value['close'] - old['close'] if value.get('close') is not None and old.get('close') is not None else None)
    return {'generated_at': now.isoformat(), 'news_window': {'start': (now-dt.timedelta(hours=24)).isoformat(),
            'end': now.isoformat()}, 'markets': markets, 'news': collect_news(now),
            'breadth': missing('상승·하락 종목 집계 제공처 미연결'),
            'investor_flows': missing('외국인·기관 순매수 제공처 미연결'),
            'fx_rates': missing('환율·금리 제공처 미연결'),
            'themes': [], 'theme_status': '검증된 구성 종목과 분류 근거 없음. 비교 데이터 축적 중',
            'us_events': [], 'us_status': '미국 사건의 한국 관련성 검증 미구현: 임의 선정하지 않음',
            'calendar': '공식 한국 거래일 달력 미연결: 직전 관측값을 직전 거래일로 확정하지 않음',
            'analysis_method': '규칙 기반 수치 비교. AI 의미 분석 사용 안 함'}


def render(report, detail=False):
    esc = lambda x: html.escape(str(x), quote=True)
    def link(url):
        return f'<a href="{esc(url)}">원문/출처</a>' if urllib.parse.urlsplit(url).scheme in ('http','https') else '링크 없음'
    parts = ['<!doctype html><html lang="ko"><meta charset="utf-8"><title>한국 시장 일일 보고서</title>',
             '<body><h1>한국 시장 일일 보고서</h1>', '<p>생성: '+esc(report['generated_at'])+'</p>',
             '<h2>오늘 시장 흐름 요약</h2>']
    for symbol, value in report['markets'].items():
        if value['status'] == 'missing':
            parts.append(f'<p>{symbol}: 수집 실패 — {esc(value["reason"])} {link(value["source"])}</p>')
            continue
        returns = ', '.join(f'{n}거래일: '+ ('비교 데이터 축적 중' if r is None else f'{r:.2f}%') for n,r in value['returns'].items())
        parts.append(f'<p>{symbol}: {esc(value["date"])} 종가 {value["close"]}, {returns}; {esc(value["note"])}; '
                     f'전 보고서 데이터 재사용: {value["reused"]}. {link(value["source"])}</p>')
    for field in ('breadth','investor_flows','fx_rates'):
        parts.append('<p>'+esc(report[field]['reason'])+'</p>')
    parts += ['<h2>주목받는 한국 업종·테마</h2>', '<table><tr><th>분류</th><th>뉴스 관심도</th><th>가격 강도</th><th>거래 참여도</th></tr><tr><td colspan="4">'+esc(report['theme_status'])+'</td></tr></table>',
              '<h2>중요 뉴스 후보</h2>', '<p>'+esc(report['news'].get('deduplication','수집 실패: '+report['news'].get('reason','')))+'</p>']
    for item in report['news']['items'][:5]:
        parts += ['<article><h3>'+esc(item['title'])+'</h3>', '<p>'+esc(item['source'])+' | '+esc(item['published'])+' '+link(item['url'])+'</p>']
        for key in ('facts','selection_reason','industry_theme','impact_path','market_reaction','counter_evidence','follow_up','body_status'):
            parts.append('<p>'+esc(key)+': '+esc(item[key])+'</p>')
        if detail:
            parts.append('<pre>'+esc(item['body'])+'</pre>')
        parts.append('</article>')
    parts += ['<h2>미국 시장의 굵직한 변화</h2><p>'+esc(report['us_status'])+'</p>',
              '<h2>오늘 확인할 일정과 불확실성</h2><p>'+esc(report['calendar'])+'</p>',
              '<p>'+esc(report['analysis_method'])+' 관측된 강세는 상승 예측 또는 매수 추천이 아닙니다.</p>',
              '<h2>상세 보고서</h2><p>동일 실행 폴더의 detail.html 및 data.json (로컬 파일; 수신자가 접근 가능한 공개 링크 없음)</p></body></html>']
    return '\n'.join(parts)


def save(report, root):
    stamp = dt.datetime.fromisoformat(report['generated_at']).strftime('%Y-%m-%d/%H%M%S-%f')
    target = Path(root) / stamp
    target.mkdir(parents=True, exist_ok=False)
    (target/'data.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    for name, detail in [('detail.html',True),('email-preview.html',False)]:
        (target/name).write_text(render(report, detail), encoding='utf-8')
    return target


class SendLedger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('CREATE TABLE IF NOT EXISTS deliveries (id TEXT PRIMARY KEY, status TEXT, error TEXT)')

    def reserve(self, key):
        try:
            with self.db:
                self.db.execute('INSERT INTO deliveries VALUES (?, ?, ?)', (key,'pending',''))
            return True
        except sqlite3.IntegrityError:
            return False

    def finish(self, key, status):
        with self.db:
            self.db.execute('UPDATE deliveries SET status=? WHERE id=?', (status,key))
