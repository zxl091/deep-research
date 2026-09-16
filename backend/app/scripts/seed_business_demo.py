"""确定性的虚构演示数据；重复执行不新增重复记录，不覆盖已有记录。"""
import sys
from pathlib import Path
from datetime import date
from uuid import uuid5, NAMESPACE_URL
from dotenv import load_dotenv

APP = Path(__file__).resolve().parents[1]
load_dotenv(APP.parent / '.env')
sys.path.insert(0, str(APP))
from core.database import SessionLocal
from models.industry_data import CompanyData, IndustryStats, PolicyData

TAG = 'DEMO_BUSINESS_V1'
NOTE = '虚构演示数据，仅用于功能测试，不代表真实企业或行业情况。'
COMPANIES = [
    ('智行交通（演示）', '智慧交通', [20, 25, 32]),
    ('路云交通（演示）', '智慧交通', [12, 15, 18]),
    ('星河软件（演示）', '企业软件', [30, 36, 45]),
    ('青禾软件（演示）', '企业软件', [10, 12, 15]),
    ('远川能源（演示）', '新能源', [40, 44, 48]),
    ('澄光能源（演示）', '新能源', [25, 30, 36]),
]


def fixtures():
    rows = []
    for name, industry, revenues in COMPANIES:
        for year, revenue in zip(range(2023, 2026), revenues):
            rows.append(CompanyData(
                id=uuid5(NAMESPACE_URL, f'{TAG}/company/{name}/{year}'),
                company_name=name, industry=industry, year=year, quarter=None,
                revenue=revenue, net_profit=round(revenue * .1, 2), gross_margin=30,
                employees=revenue * 20, data_source=TAG + '（虚构演示）',
                extra_data={'demo_dataset': TAG, 'notice': NOTE, 'period': '全年'},
            ))
    for industry in sorted({c[1] for c in COMPANIES}):
        for index, year in enumerate(range(2023, 2026)):
            revenues = [c[2][index] for c in COMPANIES if c[1] == industry]
            for metric, value, unit in [('样本企业营收合计', sum(revenues), '亿元'), ('样本企业数量', len(revenues), '家')]:
                rows.append(IndustryStats(
                    id=uuid5(NAMESPACE_URL, f'{TAG}/stats/{industry}/{year}/{metric}'),
                    industry_name=industry, metric_name=metric, metric_value=value,
                    unit=unit, year=year, region='演示样本', source=TAG,
                    notes=NOTE + '本指标仅汇总演示样本，不是全行业规模。',
                ))
        for year in [2024, 2025]:
            rows.append(PolicyData(
                id=uuid5(NAMESPACE_URL, f'{TAG}/policy/{industry}/{year}'),
                policy_name=f'【虚构演示】{year}年{industry}试点支持方案',
                policy_number=f'DEMO-{industry}-{year}', department='虚构演示管理部门',
                level='演示级', publish_date=date(year, 3, 1), effective_date=date(year, 4, 1),
                category='试点支持', industry=industry, summary=NOTE + '示例内容：支持样本项目开展数字化试点。',
                key_points={'demo_dataset': TAG, 'notice': NOTE}, impact_level='一般',
            ))
    return rows


def seed():
    added = {}
    with SessionLocal() as db:
        for row in fixtures():
            name = row.__tablename__
            added.setdefault(name, 0)
            existing = db.get(type(row), row.id)
            if existing is None:
                db.add(row)
                added[name] += 1
        db.commit()
    return added


if __name__ == '__main__':
    print(seed())
