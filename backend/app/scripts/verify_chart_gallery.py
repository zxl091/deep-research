"""十类虚构图表的真实 Docker 渲染验收，不消耗模型，不写业务数据库。"""
import asyncio
import base64
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from chart_test_fixtures import chart_fixtures
from service.deep_research_v2.chart_contract import validate_charts,render_code
from service.deep_research_v2.sandbox import execute_isolated


async def main():
    folder=Path(__file__).resolve().parents[3]/'.runtime/chart-gallery'
    folder.mkdir(parents=True,exist_ok=True)
    specs,sources=chart_fixtures();results=[]
    for spec in specs:
        valid,errors=validate_charts([spec],sources)
        assert not errors,errors
        result=await execute_isolated(render_code(valid[0]))
        assert result['success'] and len(result['charts'])==1,(spec['type'],result.get('error'))
        assert not result.get('error'),result.get('error')
        png=base64.b64decode(result['charts'][0]);assert png.startswith(b'\x89PNG\r\n\x1a\n')
        (folder/(spec['type']+'.png')).write_bytes(png)
        results.append({'type':spec['type'],'points':len(spec['points']),'bytes':len(png),'sandbox':result['sandbox'],'passed':True})
        print('PASS',spec['type'],len(png),flush=True)
    (folder/'results.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__=='__main__':asyncio.run(main())
