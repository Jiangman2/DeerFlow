"""Regression checks for missing dates, source units and chart data integrity."""
import copy
import json
from pathlib import Path
import unittest
from fetch_market_history import normalize, build

ROOT=Path(__file__).resolve().parent

class HistoryChecks(unittest.TestCase):
    def setUp(self):
        self.data=json.loads((ROOT/'data/energy_2026-11-06.json').read_text(encoding='utf-8'))
        self.raw=json.loads((ROOT/'sources/jkx_new_2026-11-06.json').read_text(encoding='utf-8'))

    def test_date_duplicate_and_invalid_numeric_rejected(self):
        with self.assertRaises(ValueError):normalize(self.raw,'2026-11-05')
        bad=copy.deepcopy(self.raw);bad['data'].append(bad['data'][0])
        with self.assertRaises(ValueError):normalize(bad,'2026-11-06')
        for value in ['nan','inf','0','-1']:
            bad=copy.deepcopy(self.raw);bad['data'][0]['close']=value
            with self.assertRaises(ValueError):normalize(bad,'2026-11-06')

    def test_weekend_and_missing_not_numeric(self):
        raw=json.loads((ROOT/'sources/jkx_new_2026-11-01.json').read_text(encoding='utf-8'))
        rows=normalize(raw,'2026-11-01')
        self.assertTrue(all(x['value'] is None and x['status']=='non_trading' for x in rows))
        missing=[x for x in self.data['observations'] if x['status']=='missing']
        self.assertEqual(len(missing),10)
        for x in missing:
            for s in self.data['history'][next(k for k in ['oil','gas','freight'] if any(q['code']==x['code'] for q in self.data[k]))]:
                if s['code']==x['code']:self.assertNotIn(x['date'],s['dates'])

    def test_units_never_spliced_or_compared(self):
        # Simulate the provider's former unit discrepancy even after correction.
        rows=copy.deepcopy(self.data['observations'])
        for r in rows:
            if r['code']=='LPG1227' and r['date']=='2026-11-06':r['unit']='元/吨'
        data=build('2026-08-06','2026-11-06',rows)
        q=next(x for x in data['freight'] if x['code']=='LPG1227')
        self.assertEqual(q['unit'],'元/吨');self.assertIsNone(q['change'])
        s=[s for s in data['history']['freight'] if s['code']=='LPG1227']
        self.assertEqual([x['unit'] for x in s],['万美元','元/吨'])
        self.assertEqual(s[0]['dates'][-1],'2026-11-05')
        self.assertEqual(s[1]['dates'],['2026-11-06'])

    def test_chart_points_and_changes_reproduce_source(self):
        self.assertEqual(self.data['start'],'2026-08-06')
        self.assertEqual(self.data['end'],'2026-11-06')
        indexed={(r['code'],r['date']):r for r in self.data['observations']}
        self.assertEqual(len(indexed),93*14)
        for kind in ['oil','gas','freight']:
            for s in self.data['history'][kind]:
                for day,value in zip(s['dates'],s['values']):
                    r=indexed[s['code'],day]
                    self.assertEqual((r['value'],r['unit']),(value,s['unit']))
            for q in self.data[kind]:
                if q['change'] is not None:
                    self.assertAlmostEqual(q['change'],(q['value']/q['previous_value']-1)*100)
        incomplete=[r for r in self.data['observations'] if not (r['code']=='LPGD59' and r['date']=='2026-11-06')]
        with self.assertRaises((ValueError,StopIteration)):build('2026-08-06','2026-11-06',incomplete)

if __name__=='__main__':unittest.main()
