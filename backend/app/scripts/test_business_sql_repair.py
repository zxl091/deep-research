"""真实只读 PostgreSQL 执行，模型替身复现浮点 ROUND 错误和权限边界。"""
import unittest
from unittest.mock import AsyncMock
import seed_business_demo
from service.text2sql_service import query_business_data, load_schema


class SQLRepairTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_type_failure_repaired_once(self):
        model = AsyncMock()
        model.complete.side_effect = [
            {'sql': 'SELECT round(CAST(25 AS double precision), 2) AS growth'},
            {'sql': 'SELECT round(CAST(25 AS numeric), 2) AS growth'},
        ]
        result = await query_business_data('计算增长率', model)
        self.assertEqual(float(result['data'][0]['growth']), 25)
        self.assertEqual(model.complete.await_count, 2)
        self.assertIn('round(double precision, integer)', model.complete.call_args.args[1])

    async def test_repair_cannot_bypass_table_scope(self):
        model = AsyncMock()
        model.complete.return_value = {'sql': 'SELECT * FROM users'}
        with self.assertRaises(ValueError):
            await query_business_data('查询企业', model)
        self.assertEqual(model.complete.await_count, 2)

    async def test_schema_contains_units(self):
        table = next(t for t in load_schema() if t['table_name'] == 'company_data')
        revenue = next(c for c in table['columns'] if c['name'] == 'revenue')
        self.assertIn('亿元', revenue['comment'])

    async def test_explicit_prefix_cannot_silently_drop_brackets(self):
        model = AsyncMock()
        model.complete.side_effect = [
            {'sql': "SELECT policy_name FROM policy_data WHERE policy_name LIKE '虚构演示%'"},
            {'sql': "SELECT policy_name FROM policy_data WHERE policy_name LIKE '【虚构演示】%'"},
        ]
        result = await query_business_data('政策名称以【虚构演示】开头', model)
        self.assertEqual(model.complete.await_count, 2)
        self.assertTrue(all(r['policy_name'].startswith('【虚构演示】') for r in result['data']))
        self.assertIn('筛选条件丢失', model.complete.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
