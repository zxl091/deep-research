"""真实 Docker 隔离验收，使用本机已有镜像，不下载或安装依赖。"""
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import unittest

MODULE = Path(__file__).resolve().parents[1] / 'service/deep_research_v2/sandbox.py'
spec = importlib.util.spec_from_file_location('research_sandbox', MODULE)
sandbox = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sandbox)


class SandboxTests(unittest.IsolatedAsyncioTestCase):
    async def run_code(self, code, **kwargs):
        return await sandbox.execute_isolated(code, image='python:3.11-slim', **kwargs)

    async def test_normal_calculation(self):
        result = await self.run_code('print(sum([40, 50])); print(round((50 / 40 - 1) * 100))')
        self.assertTrue(result['success'], result)
        self.assertEqual(result['output'], '90\n25\n')

    async def test_host_files_and_secrets_absent(self):
        canary = Path(__file__).with_name('sandbox-canary.tmp')
        canary.write_text('HOST_ONLY_TEST_CANARY', encoding='utf-8')
        os.environ['SANDBOX_HOST_SECRET'] = 'HOST_ONLY_TEST_SECRET'
        try:
            result = await self.run_code('import os\nprint(os.getenv("SANDBOX_HOST_SECRET"))\n'
                f'print(os.path.exists({str(canary)!r}))\n'
                'print(os.path.exists("/var/run/docker.sock"))\nprint(os.getuid())\n')
            self.assertTrue(result['success'], result)
            self.assertEqual(result['output'], 'None\nFalse\nFalse\n65534\n')
        finally:
            canary.unlink(missing_ok=True)
            os.environ.pop('SANDBOX_HOST_SECRET', None)

    async def test_network_disabled(self):
        result = await self.run_code('import socket\ns = socket.socket()\ns.settimeout(2)\ns.connect(("1.1.1.1", 443))')
        self.assertFalse(result['success'], result)
        self.assertIn('Network is unreachable', result['error'])

    async def test_root_read_only_and_tmp_discarded(self):
        result = await self.run_code('open("/sandbox-forbidden", "w").write("x")')
        self.assertFalse(result['success'], result)
        self.assertTrue('Read-only' in result['error'] or 'Permission denied' in result['error'])
        first = await self.run_code('open("/tmp/sandbox-marker", "w").write("x")')
        second = await self.run_code('import os\nprint(os.path.exists("/tmp/sandbox-marker"))')
        self.assertTrue(first['success'], first)
        self.assertEqual(second['output'], 'False\n')

    async def test_timeout(self):
        result = await self.run_code('while True: pass', timeout=2)
        self.assertFalse(result['success'])
        self.assertIn('超过 2 秒', result['error'])

    async def test_memory_limit(self):
        result = await self.run_code('x = bytearray(1024 * 1024 * 1024)', memory_mb=128, timeout=15)
        self.assertFalse(result['success'], result)
        self.assertTrue('137' in result['error'] or 'MemoryError' in result['error'], result)

    async def test_process_limit(self):
        result = await self.run_code('import subprocess\nchildren=[]\ntry:\n'
            ' for _ in range(100): children.append(subprocess.Popen(["sleep", "20"]))\n'
            'except OSError as e:\n print("limited", len(children), e.errno)\n'
            'finally:\n for child in children: child.terminate()\n'
            ' for child in children: child.wait()\n', timeout=15)
        self.assertTrue(result['success'], result)
        self.assertIn('limited', result['output'])
        self.assertLess(int(result['output'].split()[1]), 32)

    async def test_output_bounded(self):
        result = await self.run_code('print("x" * 40000)')
        self.assertFalse(result['success'])
        result = await self.run_code('import os\nwhile True: os.write(1, b"x" * 16384)', timeout=15)
        self.assertFalse(result['success'])
        self.assertIn('输出超过上限', result['error'])

    async def test_missing_image_fails_closed(self):
        result = await sandbox.execute_isolated('print(1)', image='research-sandbox-intentionally-absent:qa')
        self.assertFalse(result['success'])
        self.assertEqual(result['output'], '')

    async def test_cancel_cleans_container(self):
        task = asyncio.create_task(self.run_code('while True: pass'))
        await asyncio.sleep(2)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        result = await asyncio.create_subprocess_exec('docker', 'ps', '-aq', '--filter', 'name=research-sandbox-', stdout=asyncio.subprocess.PIPE)
        output, _ = await result.communicate()
        self.assertEqual(output.strip(), b'', '测试结束后不应留下沙箱容器')


if __name__ == '__main__':
    unittest.main(verbosity=2)
