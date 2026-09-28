"""Render current archived WorkBench measurements; no API calls or inferred wins."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ARCHIVES = ROOT / 'review/evidence/live-workbench-20260928'
CAMPAIGNS = [
    ('pilot-20260928-openai-responses', 'Development: 12 tasks'),
    ('heldout-20260928-baseline', 'Initial baseline: 60 tasks, subsequently inspected'),
    ('diagnostic-20260928-grounded', 'Guidance diagnostic: the same 60 tasks'),
    ('confirmation-20260928-baseline', 'Fresh baseline: next 60 tasks, incomplete'),
]
ARMS = [('reference', 'Reference'), ('foundry_native_serial', 'Foundry native'),
        ('foundry_langgraph_serial', 'Foundry LangGraph')]


def main():
    lines = ['# Live WorkBench measurements', '',
             'Recorded 2026-09-28. These are measured results, not estimates of a win.', '',
             'All successful inference used `gpt-5.4-mini-2026-03-17`, low reasoning,',
             '2,048 output tokens/request, at most 20 calls/task, one repetition,',
             'serial tool dispatch and the pinned official v2 scorer. Native and',
             'LangGraph arms retain Foundry controls with a synthetic-task L4 policy.',
             'The reference is the upstream structured loop using the same transport.', '',
             '| Campaign | Arm | Correct / attempted (planned) | Accuracy | Unwanted effects | Errors | p50 / p95 seconds | Upper cost USD |',
             '| --- | --- | --- | --- | --- | --- | --- | --- |']
    summaries = {}
    for name, label in CAMPAIGNS:
        summary = json.loads((ARCHIVES/name/'summary.json').read_text())
        summaries[name] = summary
        for arm, display in ARMS:
            a = summary['aggregates']['openai/'+arm]
            lines.append(f"| [{label}](evidence/live-workbench-20260928/{name}/summary.json) | {display} | "
                         f"{a['correct']}/{a['attempted']} ({a['planned']}) | {100*a['accuracy_among_attempted']:.2f}% | "
                         f"{a['unwanted_side_effects']} | {a['errors']} | {a['p50_latency_s']:.3f} / {a['p95_latency_s']:.3f} | "
                         f"{a['cost_usd_upper']:.6f} |")
    lines += ['', '## Interpretation and limits', '',
              '- The initial 60 tasks were later inspected for failure diagnosis. The guidance',
              '  diagnostic reuses them and also follows core repairs; it cannot isolate prompt',
              '  efficacy or serve as untouched confirmation.',
              '- Both fresh profiles were frozen before inference on the next 60 task IDs.',
              '  Connection failures exhausted the preset continuation allowance at 179/180',
              '  baseline attempts. The guidance profile was not run. No profile improvement',
              '  or complete-baseline result is claimed. Missing attempts are not successes.',
              '- Failures remain in the denominator and retain executed effects and known cost.',
              '  Setup failures and transport errors are archived too. Nothing was rerun to',
              '  replace a bad score. Small samples, task-template dependence and one repetition',
              '  limit inference; paired bootstrap intervals are exploratory.',
              '- End-to-end latency includes provider/network time and shared-workstation load.',
              '  The p95 can change sharply when timeouts exceed 5% of a group. These timings',
              '  do not isolate orchestration overhead or establish distributed throughput.',
              '- Costs use reported usage at base input/output prices plus conservative full',
              '  reservations for unknown requests. They are not provider invoices. Total ledger',
              '  accounting is $16.71967550 against the $25 cap; $7.442336 is unknown reservations.',
              '- Anthropic setup was rejected; the diagnostic identified a required workspace',
              '  header. There is no successful Claude comparison or cross-provider conclusion.',
              '- WorkBench unwanted effects are an official task-state metric, not a complete',
              '  security, authorization, injection-resistance or compliance measurement.',
              '- The best pinned upstream saved run is 674/690 (97.68%), with 13 unwanted-effect',
              '  cases. It uses a different model/harness and the full distribution. Comparing',
              '  these subset percentages to it does not establish a win.', '',
              '## Paired exploratory outcomes', '',
              '| Campaign | Runtime minus reference | Paired tasks | Accuracy difference | Bootstrap 95% interval |',
              '| --- | --- | --- | --- | --- |']
    for name, label in CAMPAIGNS:
        for key, a in summaries[name].get('paired', {}).items():
            lo, hi = a['exploratory_paired_bootstrap_95_interval']
            lines.append(f"| {label} | {key.removeprefix('openai/')} | {a['paired_tasks']} | "
                         f"{100*a['accuracy_difference']:+.2f} pp | [{100*lo:+.2f}, {100*hi:+.2f}] pp |")
    lines += ['', '## Evidence and reproduction', '',
              'Run `python3 review/evidence/audit_live_workbench.py` to verify archive hashes,',
              'coverage and aggregate arithmetic, then `python3 review/evidence/render_live_report.py`.',
              'The audit recounts stored official scores; it does not rerun the official scorer.',
              'See [the CSV](evidence/live-workbench-20260928/derived/live_results.csv),',
              '[per-attempt data](evidence/live-workbench-20260928/derived/per_attempt.csv),',
              '[audit](evidence/live-workbench-20260928/derived/audit_results.json), and',
              '[harness instructions](../benchmarks/workbench/README.md).',
              'Each campaign directory includes its manifest, environment, summary, archive index',
              'and original per-attempt bytes. The WorkBench license accompanies the archives.', '']
    (ROOT/'review/LIVE_BENCHMARK_RESULTS.md').write_text('\n'.join(lines))


if __name__ == '__main__':
    main()
