"""以验收任务已检索的原句验证图表合同与容器渲染，不冒充模型生成。"""
import asyncio
import base64
import json
from pathlib import Path
import sys
from dotenv import load_dotenv

APP=Path(__file__).resolve().parents[1]
ROOT=APP.parents[1]
load_dotenv(APP.parent/'.env');sys.path.insert(0,str(APP))
from core.database import SessionLocal
from sqlalchemy import text
from service.deep_research_v2.chart_contract import validate_charts,render_code
from service.deep_research_v2.sandbox import execute_isolated


async def main():
    with SessionLocal() as db:
        state=db.execute(text('select state from assistant_runs where id=:id'),{'id':'36f02cbf-0097-4165-9950-ba22342aa7ca'}).scalar_one()
    points=[]
    data=[('2023',949.5,'http://www.chinasihan.com/news/cysj/16313.html','2023 年,新能源汽车产销量分别达到 958.7 万辆和 949.5 万辆'),
          ('2024',1286.6,'https://www.evlook.com/sales','2024年全年,新能源汽车产销累计完成1288.8万辆和1286.6万辆')]
    for year,value,url,quote in data:
        points.append(dict(label=year,period=year,metric='新能源汽车销量',unit='万辆',scope='中国新能源汽车产销统计',period_basis='全年',value_kind='actual',value=value,source_url=url,quote=quote))
    specs,errors=validate_charts([{'title':'中国新能源汽车全年销量（原文数据渲染验收）','type':'line','points':points}],state['source_snapshots'])
    assert not errors,errors
    result=await execute_isolated(render_code(specs[0]))
    assert result['success'] and len(result['charts'])==1,result['error']
    assert not result['error'],result['error']
    (ROOT/'docs/研究原文数据绘图验收.png').write_bytes(base64.b64decode(result['charts'][0]))
    (ROOT/'docs/grounded-chart-result.json').write_text(json.dumps({'status':'passed','selection':'人工选定的回归测试点，非模型验收结果','contract':specs[0],'sandbox':result['sandbox'],'output':result['output']},ensure_ascii=False,indent=2),encoding='utf-8')
    print('原文匹配、相同单位与期间口径、Docker 渲染：通过；数据点 2，PNG 1')


if __name__=='__main__':asyncio.run(main())
