"""专用镜像真实计算与中文绘图验收，不调用外部模型。"""
import asyncio
import base64
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location('sandbox', ROOT / 'backend/app/service/deep_research_v2/sandbox.py')
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)

CODE = '''
sns.set_theme(style='whitegrid')
df = pd.DataFrame({'年份': [2023, 2024, 2025], '营收': [32, 40, 50]})
growth = (df['营收'].iloc[-1] / df['营收'].iloc[-2] - 1) * 100
print(json.dumps({'增长率': round(growth, 2), '合计': float(np.sum(df['营收']))}, ensure_ascii=False))
fig, ax = plt.subplots(figsize=(8, 4.5))
ax.plot(df['年份'], df['营收'], marker='o', color='#4277b9', linewidth=2)
ax.set_title('沙箱中文绘图验收：演示营收趋势')
ax.set_xlabel('年份')
ax.set_ylabel('营收（亿元，虚构演示数据）')
ax.set_xticks(df['年份'])
for year, revenue in zip(df['年份'], df['营收']):
    ax.annotate(str(revenue), (year, revenue), xytext=(0, 8), textcoords='offset points', ha='center')
fig.tight_layout()
'''


async def main():
    result = await sandbox.execute_isolated(CODE)
    metadata = {k: v for k, v in result.items() if k != 'charts'}
    metadata['chart_count'] = len(result['charts'])
    (ROOT / 'docs/sandbox-plot-result.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(metadata, ensure_ascii=False))
    assert result['success'], result['error']
    data = json.loads(result['output'])
    assert data == {'增长率': 25.0, '合计': 122.0}, data
    assert len(result['charts']) == 1
    assert 'Glyph' not in (result['error'] or ''), '存在中文缺字警告'
    (ROOT / 'docs/沙箱中文绘图验收.png').write_bytes(base64.b64decode(result['charts'][0]))


if __name__ == '__main__':
    asyncio.run(main())
