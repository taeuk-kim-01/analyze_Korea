import datetime as dt
import tempfile
import unittest
from unittest.mock import patch
from korea_daily.core import KST, SendLedger, attention_change, compare_return, index_history, parse_news, build, save

class CoreTests(unittest.TestCase):
    def test_window_dedup_and_future(self):
        now=dt.datetime(2026,10,28,8,tzinfo=KST)
        def item(title, date):
            return f'<item><title>{title}</title><pubDate>{date}</pubDate></item>'
        rss='<rss><channel>'+''.join([item('same','Tue, 27 Oct 2026 23:00:00 +0000')]*2+[item('old','Mon, 26 Oct 2026 22:59:59 +0000'),item('future','Wed, 28 Oct 2026 00:00:00 +0000')])+'</channel></rss>'
        self.assertEqual(len(parse_news(rss.encode(),now)),1)
    def test_returns(self):
        self.assertAlmostEqual(compare_return([100,110],[100,105])['1']['excess'],5)
        self.assertIsNone(compare_return([100],[100])['20'])
        self.assertIsNone(attention_change(3,[1]))
        self.assertEqual(attention_change(4,[2]*7),100)
    @patch('korea_daily.core.fetch')
    def test_exclude_today(self,fetch):
        fetch.return_value=b'<chart><item data="20261027|1|1|1|100|5"/><item data="20261028|1|1|1|200|10"/></chart>'
        result=index_history('KOSPI',dt.datetime(2026,10,28,8,tzinfo=KST))
        self.assertEqual(result['close'],100)
        self.assertEqual(result['freshness'],'unverified')
    @patch('korea_daily.core.fetch',side_effect=OSError('blocked'))
    def test_missing_no_fake_numbers(self,fetch):
        report=build(dt.datetime(2026,10,28,8,tzinfo=KST))
        self.assertNotIn('close',report['markets']['KOSPI'])
        self.assertEqual(report['news']['items'],[])
        with tempfile.TemporaryDirectory() as directory:
            target=save(report,directory)
            self.assertIn('수집 실패',(target/'email-preview.html').read_text())
    def test_send_reservation_persists_after_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger=SendLedger(directory+'/db')
            self.assertTrue(ledger.reserve('same'))
            ledger.finish('same','failed_or_uncertain')
            self.assertFalse(SendLedger(directory+'/db').reserve('same'))

if __name__=='__main__': unittest.main()
