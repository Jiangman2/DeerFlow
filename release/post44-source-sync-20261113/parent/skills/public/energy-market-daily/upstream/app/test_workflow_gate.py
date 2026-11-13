"""Scheduling boundaries and single-request probe, with no real network."""
import datetime as dt
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import workflow_gate as gate

class FixedDateTime(dt.datetime):
    @classmethod
    def now(cls,tz=None):return cls(2026,11,10,6,0,tzinfo=gate.TZ)

class GateChecks(unittest.TestCase):
    def test_pause_never_calls_network(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir()
            (root/'config/schedule.json').write_text(json.dumps({'enabled':False}),encoding='utf-8')
            with patch.object(gate,'PACKAGE',root),patch.object(gate.requests,'get') as get,redirect_stdout(io.StringIO()) as out:
                gate.probe()
        self.assertEqual(json.loads(out.getvalue())['status'],'PAUSED');get.assert_not_called()

    def test_one_probe_no_retry(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/'config').mkdir();(root/'app').mkdir();state=root/'app/state';state.mkdir()
            (root/'config/schedule.json').write_text(json.dumps({'enabled':True,'weekdays':['TU'],'start':'06:00','end':'23:55'}),encoding='utf-8')
            with patch.object(gate,'PACKAGE',root),patch.object(gate,'ROOT',root/'app'),patch.object(gate,'STATE',state),patch.object(gate.dt,'datetime',FixedDateTime),patch.object(gate,'endpoint',return_value=('http://example.invalid','test')),patch.object(gate.requests,'get',side_effect=RuntimeError('network unavailable')) as get,redirect_stdout(io.StringIO()) as out:
                gate.probe()
            self.assertEqual(json.loads(out.getvalue())['status'],'WAIT_DATA');self.assertEqual(get.call_count,1)

if __name__=='__main__':unittest.main()
