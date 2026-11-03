"""Offline LLM_PERF report. Standard library only; never connects to a service."""
import argparse
import csv
import json
import math
from collections import Counter, defaultdict
from pathlib import Path


def percentile(values, fraction):
    values = sorted(values)
    return None if not values else values[max(0, math.ceil(len(values) * fraction) - 1)]


def collect(paths):
    calls = {}
    errors = Counter()
    for path in paths:
        with open(path, 'rb') as stream:
            while True:
                line = stream.readline(65537)
                if not line:
                    break
                if len(line) > 65536:
                    errors['oversized_lines'] += 1
                    while line and not line.endswith(b'\n'):
                        line = stream.readline(65537)
                    continue
                if b'LLM_PERF ' not in line:
                    continue
                try:
                    row = json.loads(line.split(b'LLM_PERF ', 1)[1])
                    if row.get('schema') != 1 or row.get('event') not in ('start', 'end'):
                        raise ValueError('Unsupported record')
                    key = tuple(row[k] for k in ('pod', 'process_id', 'llm_call_id'))
                    hash(key)
                    if not all(isinstance(x, str) and x for x in key):
                        raise ValueError('Missing identity')
                    if not isinstance(row.get('timestamp'), (int, float)) or not math.isfinite(row['timestamp']):
                        raise ValueError('Invalid timestamp')
                except (ValueError, KeyError, TypeError, AttributeError):
                    errors['invalid_records'] += 1
                    continue
                pair = calls.setdefault(key, {})
                event = row['event']
                if event in pair:
                    errors['duplicate_records' if pair[event] == row else 'conflicting_records'] += 1
                else:
                    pair[event] = row
    return calls, dict(errors)


def summarize(calls):
    groups = defaultdict(list)
    details = []
    timeline = []
    for pair in calls.values():
        start, end = pair.get('start'), pair.get('end')
        row = dict(end or start)
        row['has_start'] = start is not None
        row['has_end'] = end is not None
        row['outcome'] = row.get('outcome', 'unfinished')
        details.append(row)
        groups[(row['batch'], row['model'], row['mode'])].append(row)
    summary = []
    for (batch, model, mode), rows in sorted(groups.items()):
        completed = [r for r in rows if r['has_end'] and r['outcome'] == 'completed']
        ended = [r for r in rows if r['has_end']]
        stats = dict(batch=batch, model=model, mode=mode, observed_calls=len(rows),
                     completed=len(completed), unfinished=sum(not r['has_end'] for r in rows),
                     missing_start=sum(not r['has_start'] for r in rows),
                     outcomes=dict(Counter(r['outcome'] for r in rows)),
                     errors=dict(Counter(r.get('error_class') for r in ended if r.get('error_class'))),
                     completed_fraction=len(completed) / len(rows))
        for field in ('first_effective_ms', 'first_reasoning_ms', 'first_content_ms', 'first_tool_ms',
                      'total_ms', 'queue_ms', 'service_ms'):
            values = [r[field] for r in completed if isinstance(r.get(field), (int, float))]
            stats[field] = dict(samples=len(values), p50=percentile(values, .5),
                                p95=percentile(values, .95), p99=percentile(values, .99))
        left = min(r.get('arrived_epoch', r['timestamp']) for r in rows)
        right = max(r['timestamp'] for r in rows)
        seconds = max(0, right - left)
        stats['observation_seconds'] = seconds
        stats['completed_per_second'] = len(completed) / seconds if seconds else None
        for field in ('input_tokens', 'output_tokens', 'reasoning_tokens'):
            values = [r[field] for r in completed if type(r.get(field)) is int]
            stats[field] = dict(samples=len(values), sum=sum(values) if values else None)
        output = stats['output_tokens']
        stats['reported_output_tokens_per_second'] = output['sum'] / seconds if seconds and output['samples'] == len(completed) and completed else None
        # Closed, observed adapter intervals only. Missing end records must not
        # be guessed to have ended at the file boundary. This is a lower bound.
        events = []
        for row in ended:
            admitted = row.get('admitted_epoch')
            if isinstance(admitted, (float, int)) and admitted < row['timestamp']:
                events.extend(((admitted, 1), (row['timestamp'], -1)))
        current = peak = 0
        for timestamp, change in sorted(events):
            current += change
            peak = max(peak, current)
            timeline.append(dict(batch=batch, model=model, mode=mode, timestamp=timestamp,
                                 observed_closed_call_concurrency=current))
        stats['observed_closed_call_peak_concurrency'] = peak
        stats['concurrency_scope'] = 'collected_pods_only; excludes_missing_ends; requires_synchronized_clocks'
        summary.append(stats)
    return details, summary, timeline


def write_csv(path, rows):
    keys = sorted({key for row in rows for key in row})
    with path.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('logs', nargs='+', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=False)
    calls, diagnostics = collect(args.logs)
    details, summary, timeline = summarize(calls)
    write_csv(args.out / 'calls.csv', details)
    write_csv(args.out / 'concurrency.csv', timeline)
    report = dict(diagnostics=diagnostics, groups=summary, root_cause_proven=False,
                  latency_scope='gateway_adapter_observed_not_provider_compute',
                  percentiles_scope='completed_calls_with_available_measurement',
                  token_rate_scope='aggregate_reported_output_tokens_over_observation_window_not_decode_speed')
    (args.out / 'summary.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if calls and not diagnostics.get('conflicting_records') and not diagnostics.get('invalid_records') else 1


if __name__ == '__main__':
    raise SystemExit(main())
