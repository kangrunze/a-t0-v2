import json, statistics
from collections import Counter

f = r'd:\project\a-t0-v2\outputs\backtest\689009_689009_MR1_默认_compare_2025-07-28_2026-07-22_report.json'
with open(f, encoding='utf-8') as fp:
    r = json.load(fp)

# 所有交易的 signal_source 和 status 分布
print('=== 所有交易 status 分布 ===')
all_st = Counter()
mr_open_count = 0
mr_pairing_count = 0
tf_count = 0
for dr in r.get('daily_results', []):
    for t in dr.get('trades', []):
        st = t.get('status', '')
        all_st[st] += 1
        rules = ' '.join(t.get('rules_fired', []))
        if 'MR极值' in rules and st == 'open':
            mr_open_count += 1
        elif 'MR平仓' in rules:
            mr_pairing_count += 1
        elif 'MR' not in rules:
            tf_count += 1

print(f'status分布: {dict(all_st)}')
print(f'MR开仓(open腿): {mr_open_count}')
print(f'MR平仓触发: {mr_pairing_count}')
print(f'非MR交易: {tf_count}')

# 找MR平仓的sell交易
print('\n=== MR平仓sell交易 ===')
cnt = 0
for dr in r.get('daily_results', []):
    for t in dr.get('trades', []):
        rules = ' '.join(t.get('rules_fired', []))
        if 'MR平仓' not in rules:
            continue
        print(f"\ndate={t.get('date')} dir={t.get('direction')} sell@{t.get('fill_price'):.2f} "
              f"hold={t.get('holding_bars')} status={t.get('status')}")
        for rl in t.get('rules_fired', [])[:3]:
            print(f"  {rl}")
        cnt += 1
        if cnt >= 5:
            break
    if cnt >= 5:
        break
if cnt == 0:
    print('无MR平仓交易！')

# 看所有sell交易的rules
print('\n=== sell交易rules样本 ===')
cnt = 0
for dr in r.get('daily_results', []):
    for t in dr.get('trades', []):
        if t.get('direction') != 'sell':
            continue
        rules = t.get('rules_fired', [])
        print(f"\ndate={t.get('date')} sell@{t.get('fill_price'):.2f} status={t.get('status')} hold={t.get('holding_bars')}")
        print(f"  rules ({len(rules)}):")
        for rl in rules[:3]:
            print(f"    {rl}")
        cnt += 1
        if cnt >= 5:
            break
    if cnt >= 5:
        break
